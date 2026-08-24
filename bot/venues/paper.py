"""Paper execution venue: fills orders against the local portfolio at the
decision price with adverse slippage and fees. No exchange, no keys, no real
money — this is what the live loop uses until signed testnet orders land."""

from __future__ import annotations

from ..core.models import Fill, Order, costed_fill
from ..core.portfolio import Portfolio


class PaperVenue:
    def __init__(self, portfolio: Portfolio, fee_bps: float = 10.0, slippage_bps: float = 5.0):
        self.portfolio = portfolio
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps

    def submit_order(self, order: Order) -> Fill:
        before = sum(p.realized_pnl for p in self.portfolio.positions.values())
        fill = costed_fill(order, self.fee_bps, self.slippage_bps)
        self.portfolio.apply_fill(fill)
        fill.realized = (
            sum(p.realized_pnl for p in self.portfolio.positions.values()) - before
        )
        return fill
