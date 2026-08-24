"""Strategy interface. Venues (backtest/live) call `on_bar` once per bar with
full context; the strategy returns zero or more orders that then pass through
the risk manager before execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from ..core.models import Bar, Order
from ..core.portfolio import Portfolio


@dataclass
class BarContext:
    ts: datetime
    symbol: str
    bar: Bar
    history: list[Bar]  # bars so far, current bar included as the last element
    portfolio: Portfolio
    equity: float  # mark-to-market equity at this bar's close


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def on_bar(self, ctx: BarContext) -> list[Order]:
        """Return orders to (possibly) execute at this bar's close."""
        raise NotImplementedError
