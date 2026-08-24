"""SMA-cross baseline strategy — the sanity benchmark every fancier strategy
(including the LLM analyst) must beat after fees."""

from __future__ import annotations

from ..core.models import Order, Side
from .base import BarContext, Strategy


def sma(values: list[float], n: int) -> float:
    window = values[-n:]
    return sum(window) / len(window)


class SMACrossStrategy(Strategy):
    name = "sma_cross"

    def __init__(self, fast: int = 12, slow: int = 48, position_frac: float = 1.0):
        if fast >= slow:
            raise ValueError("fast period must be smaller than slow period")
        self.fast = fast
        self.slow = slow
        self.position_frac = position_frac

    def on_bar(self, ctx: BarContext) -> list[Order]:
        hist = ctx.history
        if len(hist) < self.slow:
            return []

        closes = [b.close for b in hist[-self.slow :]]
        fast_ma = sma(closes, self.fast)
        slow_ma = sma(closes, self.slow)
        price = ctx.bar.close

        target_notional = (
            ctx.equity * self.position_frac if fast_ma > slow_ma else 0.0
        )
        pos = ctx.portfolio.positions.get(ctx.symbol)
        current_notional = (pos.qty if pos else 0.0) * price
        diff = target_notional - current_notional

        if abs(diff) < max(ctx.equity * 0.02, 20.0):  # rebalance-dust filter
            return []

        side = Side.BUY if diff > 0 else Side.SELL
        direction = "golden" if fast_ma > slow_ma else "death"
        return [
            Order(
                ts=ctx.ts,
                symbol=ctx.symbol,
                side=side,
                qty=abs(diff) / price,
                price=price,
                reason=f"sma{self.fast}/{self.slow} {direction} cross",
            )
        ]
