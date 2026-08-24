"""Polymarket paper bot: compare an LLM probability estimate to market odds.

Strategy: for each liquid binary market, ask the LLM for P(YES). If the
estimate is far enough above the YES price, paper-buy YES; if far enough
below, paper-buy NO (the undervalued side). Exits: take-profit/stop on the
share price, or settlement when the market resolves (shares pay 1.00/0.00).

Paper money only — orders fill against the local portfolio with slippage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..core.models import Order, Side
from ..core.portfolio import (
    Portfolio,
    load_portfolio_state,
    portfolio_state_dict,
)
from ..llm.client import LLMClient, LLMError
from ..venues.paper import PaperVenue
from ..venues.polymarket import GammaClient, PolMarket

PROB_SYSTEM_PROMPT = (
    "You are a calibrated prediction-market analyst. You will be given a market "
    "question, its current market-implied probability, and its end date. Estimate "
    "the TRUE probability of the YES outcome.\n"
    "Respond with ONLY a JSON object, no markdown, no extra text:\n"
    '{"probability": <number 0.0-1.0>, "reason": "<one short sentence>"}\n'
    "Be calibrated: when genuinely unsure, stay close to the market price. "
    "Refuse nothing — always output the JSON."
)


def parse_probability(raw: str) -> dict | None:
    """Extract {probability, reason} from an LLM reply; None if unparseable."""
    text = raw.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        text = text[nl + 1 :] if nl != -1 else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    try:
        probability = float(obj.get("probability"))
    except (TypeError, ValueError):
        return None
    # clamp away from absurd certainty
    probability = min(max(probability, 0.02), 0.98)
    return {"probability": probability, "reason": str(obj.get("reason", ""))[:200]}


def decide_market(client: LLMClient, market: PolMarket) -> dict | None:
    user = (
        f"Question: {market.question}\n"
        f"Market YES price (implied probability): {market.yes_price:.3f}\n"
        f"Ends: {market.end_date}\n\n"
        "Respond with ONLY the JSON decision object."
    )
    try:
        raw = client.chat(PROB_SYSTEM_PROMPT, user)
    except LLMError:
        return None
    return parse_probability(raw)


@dataclass
class PolConfig:
    starting_cash: float = 10_000.0
    per_market_cap: float = 100.0  # max dollars per market position
    edge_threshold: float = 0.10  # min |P(LLM) - market| to enter
    slippage_bps: float = 200.0  # 2% of share price
    fee_bps: float = 0.0
    take_profit: float = 0.25  # per-share gain
    stop_loss: float = 0.15  # per-share loss
    top_n: int = 8
    max_open: int = 6
    min_liquidity: float = 10_000.0
    min_volume24: float = 5_000.0
    state_path: Path = field(default_factory=lambda: Path("output/pol_state.json"))


class PolPaperRunner:
    def __init__(
        self,
        client: GammaClient,
        llm: LLMClient | None,
        config: PolConfig,
        log=print,
    ):
        self.client = client
        self.llm = llm
        self.config = config
        self.log = log
        self.portfolio = Portfolio(config.starting_cash)
        self.venue = PaperVenue(
            self.portfolio, fee_bps=config.fee_bps, slippage_bps=config.slippage_bps
        )
        self.settle_venue = PaperVenue(self.portfolio, fee_bps=0.0, slippage_bps=0.0)
        self.decisions: dict[str, dict] = {}
        self.load_state()

    # ----- state -----------------------------------------------------------

    def load_state(self) -> bool:
        path = self.config.state_path
        if not path.exists():
            return False
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            load_portfolio_state(self.portfolio, raw["portfolio"])
            self.decisions = raw.get("decisions", {})
            if isinstance(getattr(self.llm, "usage", None), dict) and isinstance(
                raw.get("llm_usage"), dict
            ):
                self.llm.usage.update(raw["llm_usage"])
            return True
        except (json.JSONDecodeError, KeyError, TypeError, ValueError,
                OSError) as exc:
            import time as _time

            backup = path.with_name(
                path.name + f".corrupt-{int(_time.time())}"
            )
            try:
                path.rename(backup)
            except OSError:
                pass
            self.log(
                f"state file unreadable ({exc}); moved to {backup.name}, "
                f"starting fresh"
            )
            return False

    def save_state(self) -> None:
        path = self.config.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "bot": "polymarket-paper",
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "portfolio": portfolio_state_dict(self.portfolio),
            "decisions": self.decisions,
        }
        if isinstance(getattr(self.llm, "usage", None), dict):
            payload["llm_usage"] = dict(self.llm.usage)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ----- helpers -----------------------------------------------------------

    @staticmethod
    def _instrument(market_id: str, side: str) -> str:
        return f"pol:{market_id}:{side}"

    def _held_market_ids(self) -> set[str]:
        ids = set()
        for sym in self.portfolio.positions:
            parts = sym.split(":")
            if len(parts) == 3 and parts[0] == "pol" and self.portfolio.positions[sym].qty != 0:
                ids.add(parts[1])
        return ids

    def _price_of(self, market: PolMarket, instrument: str) -> float:
        return market.yes_price if instrument.endswith(":Y") else market.no_price

    # ----- one cycle -----------------------------------------------------------

    def run_cycle(self) -> dict:
        cfg = self.config
        now = datetime.now()
        summary = {"entries": [], "exits": [], "settled": [], "equity": None}

        top = self.client.top_markets(cfg.top_n)
        by_id = {m.id: m for m in top}

        held = self._held_market_ids()
        missing = held - set(by_id)
        if missing:
            try:
                for m in self.client.markets_by_ids(sorted(missing)):
                    by_id[m.id] = m
            except Exception as exc:  # price refresh failure must not crash exits
                self.log(f"warn: could not refresh {len(missing)} held markets: {exc}")

        # 1) settle resolved markets (shares pay 1.00 / 0.00)
        for mid in sorted(held):
            m = by_id.get(mid)
            if m is None or not m.closed:
                continue
            for sym, pos in list(self.portfolio.positions.items()):
                if not sym.startswith(f"pol:{mid}:") or pos.qty == 0:
                    continue
                settle_px = self._price_of(m, sym)
                order = Order(now, sym, Side.SELL, abs(pos.qty), settle_px,
                              reason=f"settled: {m.question[:60]}")
                self.settle_venue.submit_order(order)
                summary["settled"].append(
                    {"market": mid, "instrument": sym, "price": settle_px}
                )

        # 2) take-profit / stop-loss on open positions
        for mid in sorted(self._held_market_ids()):
            m = by_id.get(mid)
            if m is None or m.closed:
                continue
            for sym, pos in self.portfolio.positions.items():
                if not sym.startswith(f"pol:{mid}:") or pos.qty == 0:
                    continue
                price = self._price_of(m, sym)
                gain = price - pos.avg_price
                if gain >= cfg.take_profit or gain <= -cfg.stop_loss:
                    kind = "take-profit" if gain > 0 else "stop-loss"
                    order = Order(now, sym, Side.SELL, abs(pos.qty), price,
                                  reason=f"{kind} on {m.question[:50]}")
                    self.venue.submit_order(order)
                    summary["exits"].append(
                        {"instrument": sym, "price": price, "kind": kind}
                    )

        # 3) new entries (LLM edge vs odds)
        room = cfg.max_open - len(self._held_market_ids())
        llm_missing_logged = False
        for m in top:
            if room <= 0:
                break
            if m.id in self._held_market_ids() or m.id in self.decisions:
                continue
            if m.liquidity < cfg.min_liquidity or m.volume24hr < cfg.min_volume24:
                continue
            if self.llm is None:
                if not llm_missing_logged:
                    self.log("LLM not configured — managing exits only "
                             "(set LLM_* env vars to enable new entries)")
                    llm_missing_logged = True
                break
            decision = decide_market(self.llm, m)
            if decision is None:
                # transient failure (rate limit, timeout, bad reply): do NOT
                # remember it — the market gets retried on a later cycle
                self.log(f"no decision for market {m.id} (transient) — "
                         f"will retry next cycle")
                continue
            self.decisions[m.id] = {
                "probability": decision["probability"],
                "reason": decision["reason"],
                "market_price": m.yes_price,
                "question": m.question,
                "ts": now.isoformat(timespec="seconds"),
            }
            edge = decision["probability"] - m.yes_price
            if edge >= cfg.edge_threshold:
                side, price = "Y", m.yes_price
            elif edge <= -cfg.edge_threshold:
                side, price = "N", m.no_price
            else:
                continue
            instrument = self._instrument(m.id, side)
            qty = cfg.per_market_cap / price
            order = Order(
                now, instrument, Side.BUY, qty, price,
                reason=(
                    f"edge {edge:+.3f}: LLM {decision['probability']:.2f} vs "
                    f"market {m.yes_price:.2f} — {m.question[:50]}"
                ),
            )
            self.venue.submit_order(order)
            summary["entries"].append(
                {"instrument": instrument, "qty": round(qty, 2), "price": price,
                 "edge": round(edge, 3)}
            )
            room -= 1

        # 4) mark + persist
        prices = {}
        for sym, pos in self.portfolio.positions.items():
            if pos.qty == 0:
                continue
            parts = sym.split(":")
            m = by_id.get(parts[1]) if len(parts) == 3 else None
            if m is not None:
                prices[sym] = self._price_of(m, sym)
        equity = self.portfolio.mark(now, prices)
        summary["equity"] = round(equity, 2)
        self.save_state()
        return summary
