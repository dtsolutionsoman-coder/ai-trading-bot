"""Core data types shared by every venue (backtest, Hyperliquid, Polymarket, Solana)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Bar:
    """One OHLCV candle."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Order:
    """Intent to trade; the venue/risk layer decides if and how it fills."""

    ts: datetime
    symbol: str
    side: Side
    qty: float  # base units, always positive; direction comes from side
    price: float  # reference price at decision time
    reason: str = ""  # human-readable rationale (kept in logs/reports)


@dataclass
class Fill(Order):
    """An executed order. `price` is the actual execution price, `fee` the paid fee."""

    fee: float = 0.0
    realized: float = 0.0  # realized P&L attributed to this fill on position reduction


def costed_fill(order: Order, fee_bps: float, slippage_bps: float) -> Fill:
    """Turn an order into a fill with adverse slippage and fees applied.

    Single source of fill math for the backtester and the paper venue so the
    two can never drift apart.
    """
    slip = slippage_bps / 1e4
    px = order.price * ((1.0 + slip) if order.side is Side.BUY else (1.0 - slip))
    fee = order.qty * px * fee_bps / 1e4
    return Fill(
        ts=order.ts,
        symbol=order.symbol,
        side=order.side,
        qty=order.qty,
        price=px,
        fee=fee,
        reason=order.reason,
    )
