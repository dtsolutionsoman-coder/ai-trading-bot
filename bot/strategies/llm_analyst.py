"""LLM analyst strategy — the 'AI brain'.

Every `every` bars, compute a compact feature summary of the recent market and
let an LLM decide buy / sell / hold with a conviction that sizes the position.
Failed or unparseable LLM responses always degrade to hold (never trade on a
broken call). Orders still pass through the risk manager like any strategy.
"""

from __future__ import annotations

import json
import math
from typing import Callable

from ..core.models import Order, Side
from ..llm.client import LLMClient, LLMError
from .base import BarContext, Strategy
from .sma_cross import sma

SYSTEM_PROMPT = (
    "You are a disciplined crypto trading analyst managing a single position.\n"
    "You receive a JSON object of market features for one asset.\n"
    "Respond with ONLY a JSON object, no markdown, no extra text:\n"
    '{"action": "buy"|"sell"|"hold", "conviction": <0.0-1.0>, "reason": "<one short sentence>"}\n'
    "Rules: 'buy' increases long exposure; 'sell' decreases it (shorting allowed); "
    "'hold' keeps current exposure. 'conviction' sizes how much of the maximum "
    "allowed position to hold (0 = flat, 1 = full). Prefer 'hold' unless several "
    "features agree in one direction. Do not chase single-bar noise. "
    "If current_position and unrealized_pnl_pct are shown, manage that exposure "
    "holistically — e.g. defend profits, cut losers, avoid doubling risk."
)


def _pct_change(closes: list[float], n: int) -> float:
    if len(closes) <= n or closes[-1 - n] == 0:
        return 0.0
    return closes[-1] / closes[-1 - n] - 1.0


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(-period, 0):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    if losses == 0.0:
        return 100.0
    rs = gains / losses
    return 100.0 - 100.0 / (1.0 + rs)


def _volatility(closes: list[float], n: int = 24) -> float:
    if len(closes) < n + 1:
        return 0.0
    rets = [
        closes[i] / closes[i - 1] - 1.0 for i in range(-n, 0) if closes[i - 1] != 0
    ]
    if not rets:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var)


def parse_decision(raw: str) -> dict:
    """Extract {action, conviction, reason} from an LLM reply; hold on failure."""
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1 :] if first_newline != -1 else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {"action": "hold", "conviction": 0.0, "reason": "unparseable response"}
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {"action": "hold", "conviction": 0.0, "reason": "invalid JSON"}
    if not isinstance(obj, dict):
        return {"action": "hold", "conviction": 0.0, "reason": "invalid JSON"}

    action = str(obj.get("action", "hold")).lower()
    if action not in ("buy", "sell", "hold"):
        action = "hold"
    try:
        conviction = float(obj.get("conviction", 0.0))
    except (TypeError, ValueError):
        conviction = 0.0
    conviction = min(max(conviction, 0.0), 1.0)
    reason = str(obj.get("reason", ""))[:200]
    return {"action": action, "conviction": conviction, "reason": reason}


class LLMAnalystStrategy(Strategy):
    name = "llm_analyst"

    def __init__(
        self,
        client: LLMClient,
        every: int = 6,
        lookback: int | None = None,
        allow_short: bool = True,
        position_frac: float = 1.0,
        context_provider: Callable[[str], dict] | None = None,
        bars_per_hour: float = 1.0,
    ):
        if every < 1:
            raise ValueError("every must be >= 1")
        self.client = client
        self.every = every
        self.allow_short = allow_short
        self.position_frac = position_frac
        self.context_provider = context_provider  # optional richer data feed

        # windows are expressed in HOURS and converted to bars, so feature
        # names stay truthful on any interval (1h, 15m, 5m, ...)
        bph = max(float(bars_per_hour), 1.0 / 60.0)
        self.bars_per_hour = bph
        self.n_1h = max(1, round(1 * bph))
        self.n_6h = max(self.n_1h, round(6 * bph))
        self.n_24h = max(self.n_6h, round(24 * bph))
        self.sma_fast = max(2, round(12 * bph))   # 12-hour SMA
        self.sma_slow = max(self.sma_fast + 1, round(48 * bph))  # 48-hour SMA
        self.vol_window = self.n_24h
        if lookback is None:
            lookback = max(96, self.sma_slow, self.n_24h + 1)
        # a caller-provided short lookback shrinks the windows to fit
        # (features stay truthful relative to the available history)
        self.n_24h = min(self.n_24h, max(1, lookback - 1))
        self.n_6h = min(self.n_6h, self.n_24h)
        self.n_1h = min(self.n_1h, self.n_6h)
        self.sma_slow = min(self.sma_slow, max(4, lookback))
        self.sma_fast = min(self.sma_fast, max(2, self.sma_slow - 1))
        self.vol_window = self.n_24h
        self.lookback = lookback
        self._bar_index = 0
        self.decisions: list[dict] = []  # audit log of every parsed decision

    def on_bar(self, ctx: BarContext) -> list[Order]:
        self._bar_index += 1
        hist = ctx.history
        if len(hist) < self.lookback or (self._bar_index % self.every) != 0:
            return []

        closes = [b.close for b in hist[-self.lookback :]]
        features = {
            "price": closes[-1],
            "chg_1h_pct": round(_pct_change(closes, self.n_1h) * 100, 3),
            "chg_6h_pct": round(_pct_change(closes, self.n_6h) * 100, 3),
            "chg_24h_pct": round(_pct_change(closes, self.n_24h) * 100, 3),
            "sma12h_sma48h_ratio": round(
                sma(closes, self.sma_fast) / sma(closes, self.sma_slow), 4
            ),
            "rsi14": round(_rsi(closes, 14), 1),  # standard 14-period RSI
            "vol_24h_pct": round(_volatility(closes, self.vol_window) * 100, 3),
        }
        if self.context_provider is not None:
            try:  # data-layer problems must never stop trading logic
                features.update(self.context_provider(ctx.symbol))
            except Exception:
                pass

        # the model must know what it already holds to manage it holistically
        pos = ctx.portfolio.positions.get(ctx.symbol)
        qty = pos.qty if pos else 0.0
        if qty != 0.0 and pos.avg_price:
            features["current_position"] = round(qty, 6)
            features["unrealized_pnl_pct"] = round(
                (closes[-1] / pos.avg_price - 1.0) * 100.0
                * (1.0 if qty > 0 else -1.0),
                2,
            )
        user_msg = (
            f"Market features JSON:\n{json.dumps(features)}\n\n"
            "Respond with ONLY the JSON decision object."
        )

        try:
            raw = self.client.chat(SYSTEM_PROMPT, user_msg)
        except LLMError:
            return []  # never trade on a failed call

        decision = parse_decision(raw)
        self.decisions.append({"ts": ctx.ts.isoformat(), **decision})

        price = ctx.bar.close
        pos = ctx.portfolio.positions.get(ctx.symbol)
        current_notional = (pos.qty if pos else 0.0) * price
        max_notional = ctx.equity * self.position_frac

        if decision["action"] == "hold":
            target_notional = current_notional
        elif decision["action"] == "buy":
            target_notional = max_notional * decision["conviction"]
        else:  # sell
            target_notional = (
                -max_notional * decision["conviction"] if self.allow_short else 0.0
            )

        diff = target_notional - current_notional
        if abs(diff) < max(ctx.equity * 0.02, 20.0):
            return []

        side = Side.BUY if diff > 0 else Side.SELL
        return [
            Order(
                ts=ctx.ts,
                symbol=ctx.symbol,
                side=side,
                qty=abs(diff) / price,
                price=price,
                reason=(
                    f"LLM {decision['action']} conv={decision['conviction']:.2f}: "
                    f"{decision['reason']}"
                ),
            )
        ]
