"""Venue interfaces. A venue is (a) market data + (b) order execution.

Backtesting, Hyperliquid testnet, Polymarket and Solana each implement these
with the same strategy/risk/portfolio stack on top.
"""

from __future__ import annotations

from typing import Protocol

from ..core.models import Bar, Fill, Order


class MarketDataClient(Protocol):
    """Read-only market data source."""

    def fetch_bars(self, symbol: str, interval: str, limit: int = 500) -> list[Bar]:
        """Return the most recent CLOSED bars, oldest first."""
        ...


class ExecutionVenue(Protocol):
    """Where orders get filled (paper simulator now; signed testnet later)."""

    def submit_order(self, order: Order) -> Fill:
        """Fill an order that already passed the risk manager."""
        ...
