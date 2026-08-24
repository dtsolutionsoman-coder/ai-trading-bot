"""Solana paper bots.

SniperPaperRunner: watches DexScreener's newest solana token listings, paper-
buys the ones that pass liquidity/volume filters, exits on take-profit,
stop-loss, or age. CopyPaperRunner: snapshots followed wallets' SPL token
balances via public RPC and mirrors balance increases/decreases on paper.

Both are paper money only. Meme-token reality check: real sniping loses money
most of the time due to fees, rug pulls, and slippage far worse than the
paper model assumes — this exists to test logic, not to print money.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..core.models import Order, Side
from ..core.portfolio import (
    Portfolio,
    load_portfolio_state,
    portfolio_state_dict,
)
from ..venues.dexscreener import DexScreenerClient, TokenPair
from ..venues.paper import PaperVenue
from ..venues.solana_rpc import SKIP_MINTS, SolanaRpcClient


@dataclass
class SniperConfig:
    starting_cash: float = 10_000.0
    per_token_cap: float = 50.0
    min_liquidity_usd: float = 5_000.0
    min_volume_h24: float = 3_000.0
    take_profit_pct: float = 0.80
    stop_loss_pct: float = 0.35
    max_age_hours: float = 24.0
    max_open: int = 10
    max_new_checks: int = 5  # token lookups per cycle (API courtesy)
    slippage_bps: float = 300.0
    fee_bps: float = 25.0
    state_path: Path = field(default_factory=lambda: Path("output/sol_sniper_state.json"))


class SniperPaperRunner:
    def __init__(self, dex: DexScreenerClient, config: SniperConfig, log=print):
        self.dex = dex
        self.config = config
        self.log = log
        self.portfolio = Portfolio(config.starting_cash)
        self.venue = PaperVenue(
            self.portfolio, fee_bps=config.fee_bps, slippage_bps=config.slippage_bps
        )
        self.seen: list[str] = []
        self.tracked: dict[str, dict] = {}
        self.load_state()

    def load_state(self) -> bool:
        path = self.config.state_path
        if not path.exists():
            return False
        raw = json.loads(path.read_text(encoding="utf-8"))
        load_portfolio_state(self.portfolio, raw["portfolio"])
        self.seen = raw.get("seen", [])[-1000:]
        self.tracked = raw.get("tracked", {})
        return True

    def save_state(self) -> None:
        path = self.config.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "bot": "solana-sniper-paper",
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "portfolio": portfolio_state_dict(self.portfolio),
            "seen": self.seen[-1000:],
            "tracked": self.tracked,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def run_cycle(self) -> dict:
        cfg = self.config
        now = datetime.now()
        summary = {"opened": [], "closed": [], "equity": None, "skipped": 0}

        try:
            tokens = self.dex.latest_solana_tokens()
        except Exception as exc:
            self.log(f"warn: token feed unavailable: {exc}")
            tokens = []

        known = set(self.seen)
        fresh = [t for t in tokens if t not in known]
        self.seen.extend(fresh)

        for addr in fresh[: cfg.max_new_checks]:
            try:
                pair = self.dex.token_pair(addr)
            except Exception as exc:
                self.log(f"warn: pair lookup failed for {addr[:10]}…: {exc}")
                continue
            if pair is None:
                summary["skipped"] += 1
                continue
            if (
                pair.liquidity_usd < cfg.min_liquidity_usd
                or pair.volume_h24 < cfg.min_volume_h24
                or len(self.tracked) >= cfg.max_open
            ):
                summary["skipped"] += 1
                continue
            qty = cfg.per_token_cap / pair.price_usd
            order = Order(
                now, pair.pair_address, Side.BUY, qty, pair.price_usd,
                reason=f"snipe new pair {pair.symbol} "
                       f"(liq ${pair.liquidity_usd:,.0f})",
            )
            fill = self.venue.submit_order(order)
            self.tracked[pair.pair_address] = {
                "token_address": addr,
                "symbol": pair.symbol,
                "entry_price": fill.price,
                "opened_at": now.isoformat(timespec="seconds"),
            }
            summary["opened"].append(
                {"symbol": pair.symbol, "qty": round(qty, 2), "price": fill.price}
            )

        prices: dict[str, float] = {}
        for key, pos in list(self.tracked.items()):
            price = None
            try:
                pair = self.dex.token_pair(pos["token_address"])
                price = pair.price_usd if pair else None
            except Exception as exc:
                self.log(f"warn: price refresh failed for {pos['symbol']}: {exc}")
            if price is not None:
                prices[key] = price

            portfolio_pos = self.portfolio.positions.get(key)
            qty_held = portfolio_pos.qty if portfolio_pos else 0.0
            if qty_held <= 0:
                self.tracked.pop(key, None)
                continue

            gain = (price / pos["entry_price"] - 1.0) if price else None
            age_h = (now - datetime.fromisoformat(pos["opened_at"])).total_seconds() / 3600
            exit_reason = None
            if gain is not None and gain >= cfg.take_profit_pct:
                exit_reason = f"take-profit {gain:+.0%}"
            elif gain is not None and gain <= -cfg.stop_loss_pct:
                exit_reason = f"stop-loss {gain:+.0%}"
            elif age_h >= cfg.max_age_hours:
                exit_reason = f"max age {age_h:.0f}h"
            elif price is None:
                exit_reason = "price data gone (treat as stopped)"

            if exit_reason:
                sell_px = price if price else pos["entry_price"] * (1 - cfg.stop_loss_pct)
                order = Order(now, key, Side.SELL, qty_held, sell_px,
                              reason=f"{exit_reason} on {pos['symbol']}")
                self.venue.submit_order(order)
                self.tracked.pop(key, None)
                summary["closed"].append(
                    {"symbol": pos["symbol"], "price": sell_px, "reason": exit_reason}
                )

        equity = self.portfolio.mark(now, prices)
        summary["equity"] = round(equity, 2)
        self.save_state()
        return summary


@dataclass
class CopyConfig:
    starting_cash: float = 10_000.0
    per_trade_cap: float = 50.0
    max_price_lookups: int = 5
    slippage_bps: float = 300.0
    fee_bps: float = 25.0
    state_path: Path = field(default_factory=lambda: Path("output/sol_copy_state.json"))


class CopyPaperRunner:
    def __init__(
        self,
        rpc: SolanaRpcClient,
        dex: DexScreenerClient,
        config: CopyConfig,
        log=print,
    ):
        self.rpc = rpc
        self.dex = dex
        self.config = config
        self.log = log
        self.portfolio = Portfolio(config.starting_cash)
        self.venue = PaperVenue(
            self.portfolio, fee_bps=config.fee_bps, slippage_bps=config.slippage_bps
        )
        self.snapshots: dict[str, dict[str, float]] = {}
        self.load_state()

    def load_state(self) -> bool:
        path = self.config.state_path
        if not path.exists():
            return False
        raw = json.loads(path.read_text(encoding="utf-8"))
        load_portfolio_state(self.portfolio, raw["portfolio"])
        self.snapshots = raw.get("snapshots", {})
        return True

    def save_state(self) -> None:
        path = self.config.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "bot": "solana-copy-paper",
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "portfolio": portfolio_state_dict(self.portfolio),
            "snapshots": self.snapshots,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _instrument(mint: str) -> str:
        return f"sol:{mint[:16]}"

    def run_cycle(self, wallets: list[str]) -> dict:
        cfg = self.config
        now = datetime.now()
        summary = {"mirrored": [], "equity": None}
        prices: dict[str, float] = {}

        for wallet in wallets:
            try:
                snap = self.rpc.wallet_tokens(wallet)
            except Exception as exc:
                self.log(f"warn: wallet {wallet[:8]}… unavailable: {exc}")
                continue

            if wallet not in self.snapshots:
                # first-ever snapshot is only a baseline: we cannot know when
                # those tokens were bought, so mirroring starts next cycle
                self.snapshots[wallet] = snap
                self.log(f"baseline snapshot stored for {wallet[:8]}…")
                continue
            prev = self.snapshots[wallet]

            changes = []
            for mint, balance in snap.items():
                if mint in SKIP_MINTS:
                    continue
                delta = balance - prev.get(mint, 0.0)
                if abs(delta) > 0:
                    changes.append((mint, delta, prev.get(mint, 0.0)))

            lookups: dict[str, float] = {}
            for mint, _delta, _prev in changes[: cfg.max_price_lookups]:
                try:
                    pair = self.dex.token_pair(mint)
                except Exception:
                    pair = None
                if pair is not None:
                    lookups[mint] = pair.price_usd
                    prices[self._instrument(mint)] = pair.price_usd

            for mint, delta, prev_balance in changes:
                instrument = self._instrument(mint)
                if delta > 0 and mint in lookups:
                    price = lookups[mint]
                    notional = min(cfg.per_trade_cap, delta * price)
                    qty = notional / price
                    order = Order(
                        now, instrument, Side.BUY, qty, price,
                        reason=f"copy buy {mint[:8]}… (+{delta:.4g} by wallet)",
                    )
                    self.venue.submit_order(order)
                    summary["mirrored"].append(
                        {"instrument": instrument, "side": "buy", "qty": round(qty, 4)}
                    )
                elif delta < 0:
                    pos = self.portfolio.positions.get(instrument)
                    if pos and pos.qty > 0:
                        frac = min(1.0, -delta / prev_balance) if prev_balance > 0 else 1.0
                        qty = pos.qty * frac
                        px = lookups.get(mint, pos.avg_price)
                        order = Order(
                            now, instrument, Side.SELL, qty, px,
                            reason=f"copy sell {mint[:8]}… ({frac:.0%} of wallet's cut)",
                        )
                        self.venue.submit_order(order)
                        summary["mirrored"].append(
                            {"instrument": instrument, "side": "sell", "qty": round(qty, 4)}
                        )

            self.snapshots[wallet] = snap

        equity = self.portfolio.mark(now, prices)
        summary["equity"] = round(equity, 2)
        self.save_state()
        return summary
