"""Funding-rate carry strategy — get paid for taking the unpopular side.

When perp funding is extremely positive, longs are paying shorts (crowded
longs): short and collect. Extremely negative: long and collect. Exit when
funding decays back into the band. Needs the data layer (`--context-db`)
for funding_ann_pct. No prediction required — this harvests a structural
payment, which is why it decorrelates from the trend/LLM books.
"""

from __future__ import annotations

from typing import Callable

from ..core.models import Order, Side
from .base import BarContext, Strategy


class FundingCarryStrategy(Strategy):
    name = "funding_carry"

    def __init__(
        self,
        provider: Callable[[str], dict],
        entry_ann_pct: float = 50.0,
        exit_ann_pct: float = 10.0,
        position_frac: float = 0.5,
        every: int = 1,
    ):
        if entry_ann_pct <= exit_ann_pct:
            raise ValueError("entry threshold must exceed exit threshold")
        self.provider = provider
        self.entry_ann_pct = entry_ann_pct
        self.exit_ann_pct = exit_ann_pct
        self.position_frac = position_frac
        self.every = every
        self._bar_index = 0

    def on_bar(self, ctx: BarContext) -> list[Order]:
        self._bar_index += 1
        if (self._bar_index % self.every) != 0:
            return []

        try:
            features = self.provider(ctx.symbol)
        except Exception:
            return []
        funding = features.get("funding_ann_pct")
        if funding is None:
            return []

        pos = ctx.portfolio.positions.get(ctx.symbol)
        cur_notional = (pos.qty if pos else 0.0) * ctx.bar.close
        cur_frac = cur_notional / ctx.equity if ctx.equity > 0 else 0.0

        if funding >= self.entry_ann_pct:
            target_frac = -1.0  # shorts collect when longs overpay
        elif funding <= -self.entry_ann_pct:
            target_frac = 1.0  # longs collect when shorts overpay
        elif abs(funding) <= self.exit_ann_pct:
            target_frac = 0.0  # decayed — bank it and go flat
        else:
            return []  # inside the band: keep the current stance as-is

        target_notional = target_frac * self.position_frac * ctx.equity
        diff = target_notional - cur_notional
        if abs(diff) < max(ctx.equity * 0.02, 20.0):
            return []

        side = Side.BUY if diff > 0 else Side.SELL
        stance = "long" if target_frac > 0 else "short" if target_frac < 0 else "flat"
        return [
            Order(
                ts=ctx.ts,
                symbol=ctx.symbol,
                side=side,
                qty=abs(diff) / ctx.bar.close,
                price=ctx.bar.close,
                reason=f"funding carry: {funding:+.0f}% ann -> {stance}",
            )
        ]
