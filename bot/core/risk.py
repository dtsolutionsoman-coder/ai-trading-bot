"""Risk manager: position caps, stop-losses, daily-loss circuit breaker.

The backtest/live loop calls, in order per bar:

1. `on_new_day(equity)` when the calendar day rolls over (resets the halt)
2. `check_halt(equity)` otherwise — trips the breaker past the daily loss limit
3. `enforce_stops(...)` — always allowed, even while halted (exits reduce risk)
4. `check_order(...)` — blocked entirely while halted; oversized orders are
   downsized to the cap rather than rejected
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import Order, Side
from .portfolio import Position, Portfolio


@dataclass
class RiskConfig:
    max_position_pct: float = 0.25  # max notional per position as fraction of equity
    stop_loss_pct: float = 0.05  # exit when position is down this % from avg entry
    daily_loss_limit_pct: float = 0.04  # halt new entries after -4% day
    min_notional_usd: float = 10.0  # ignore orders smaller than this


@dataclass
class RiskManager:
    config: RiskConfig
    day_start_equity: float = 0.0
    halted: bool = False
    stop_hits: int = 0
    rejected_orders: int = 0

    def on_new_day(self, equity: float) -> None:
        self.day_start_equity = equity
        self.halted = False

    def check_halt(self, equity: float) -> bool:
        if self.day_start_equity <= 0:
            return False
        if equity < self.day_start_equity * (1.0 - self.config.daily_loss_limit_pct):
            self.halted = True
        return self.halted

    def check_order(
        self, order: Order, equity: float, current_qty: float = 0.0
    ) -> Order | None:
        """Return a (possibly trimmed) order, or None if it must be dropped.

        The cap applies to the RESULTING position (current_qty + this order),
        so repeated orders cannot creep past the exposure limit bar by bar.
        """
        if self.halted:
            self.rejected_orders += 1
            return None
        if order.price <= 0:
            self.rejected_orders += 1
            return None

        signed = order.qty if order.side is Side.BUY else -order.qty
        resulting = current_qty + signed
        max_qty = equity * self.config.max_position_pct / order.price

        if abs(resulting) > max_qty:
            resulting = max(-max_qty, min(max_qty, resulting))
            trimmed = resulting - current_qty
            if trimmed == 0.0 or (trimmed > 0) != (order.side is Side.BUY):
                # nothing fits, or trimming would flip the trade direction
                self.rejected_orders += 1
                return None
            order = Order(
                ts=order.ts,
                symbol=order.symbol,
                side=order.side,
                qty=abs(trimmed),
                price=order.price,
                reason=f"{order.reason} [downsized to risk cap]",
            )

        if order.qty * order.price < self.config.min_notional_usd:
            self.rejected_orders += 1
            return None
        return order

    def enforce_stops(
        self,
        ts: datetime,
        positions: dict[str, Position],
        prices: dict[str, float],
    ) -> list[Order]:
        """Market-close any position breaching its stop-loss."""
        out: list[Order] = []
        for pos in positions.values():
            if pos.qty == 0:
                continue
            price = prices.get(pos.symbol)
            if price is None:
                continue
            stop = self.config.stop_loss_pct
            long_breach = pos.qty > 0 and price <= pos.avg_price * (1.0 - stop)
            short_breach = pos.qty < 0 and price >= pos.avg_price * (1.0 + stop)
            if long_breach or short_breach:
                self.stop_hits += 1
                out.append(
                    Order(
                        ts=ts,
                        symbol=pos.symbol,
                        side=Side.SELL if pos.qty > 0 else Side.BUY,
                        qty=abs(pos.qty),
                        price=price,
                        reason=f"stop-loss {stop:.0%} breach (entry {pos.avg_price:.2f})",
                    )
                )
        return out
