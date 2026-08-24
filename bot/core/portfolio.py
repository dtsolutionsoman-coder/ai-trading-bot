"""Portfolio accounting: cash, signed positions (long/short), realized P&L.

Futures-style accounting: a BUY deducts qty*price+fee from cash, a SELL adds
qty*price-fee. Equity = cash + sum(position.qty * mark_price). Works for longs
and shorts symmetrically, including position flips.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .models import Fill, Side


@dataclass
class Position:
    symbol: str
    qty: float = 0.0  # signed: positive long, negative short
    avg_price: float = 0.0  # average entry price of the open quantity
    realized_pnl: float = 0.0  # cumulative realized P&L over the position's life


@dataclass
class Portfolio:
    starting_cash: float
    cash: float = None  # type: ignore[assignment]  # set in __post_init__
    positions: dict[str, Position] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.cash is None:
            self.cash = self.starting_cash

    def equity(self, prices: dict[str, float]) -> float:
        """Mark-to-market equity given {symbol: price}."""
        pos_value = 0.0
        for pos in self.positions.values():
            price = prices.get(pos.symbol)
            if price is None:
                price = pos.avg_price  # no mark available; fall back to entry
            pos_value += pos.qty * price
        return self.cash + pos_value

    def apply_fill(self, fill: Fill) -> None:
        pos = self.positions.setdefault(fill.symbol, Position(fill.symbol))
        signed_qty = fill.qty if fill.side is Side.BUY else -fill.qty
        self.cash -= signed_qty * fill.price + fill.fee

        if pos.qty == 0 or (pos.qty > 0) == (signed_qty > 0):
            # opening or adding to the position — weighted-average entry
            new_qty = pos.qty + signed_qty
            pos.avg_price = (
                pos.avg_price * abs(pos.qty) + fill.price * abs(signed_qty)
            ) / abs(new_qty)
            pos.qty = new_qty
        else:
            # reducing, closing, or flipping
            before_qty = pos.qty
            close_qty = min(abs(signed_qty), abs(before_qty))
            if before_qty > 0:
                pos.realized_pnl += (fill.price - pos.avg_price) * close_qty
            else:
                pos.realized_pnl += (pos.avg_price - fill.price) * close_qty
            pos.qty += signed_qty
            if pos.qty == 0:
                pos.avg_price = 0.0
            elif (pos.qty > 0) != (before_qty > 0):
                # flipped through zero — remainder opened at this fill's price
                pos.avg_price = fill.price

        self.fills.append(fill)

    def mark(self, ts: datetime, eq_prices: dict[str, float]) -> float:
        eq = self.equity(eq_prices)
        self.equity_curve.append((ts, eq))
        return eq


def portfolio_state_dict(
    portfolio: Portfolio, max_fills: int = 200, max_points: int = 2000
) -> dict:
    """Serialize a portfolio to a JSON-safe dict (state files)."""
    return {
        "starting_cash": portfolio.starting_cash,
        "cash": portfolio.cash,
        "positions": {
            sym: {
                "qty": p.qty,
                "avg_price": p.avg_price,
                "realized_pnl": p.realized_pnl,
            }
            for sym, p in portfolio.positions.items()
        },
        "fills": [
            {
                "ts": f.ts.isoformat(),
                "symbol": f.symbol,
                "side": f.side.value,
                "qty": f.qty,
                "price": f.price,
                "fee": f.fee,
                "realized": f.realized,
                "reason": f.reason,
            }
            for f in portfolio.fills[-max_fills:]
        ],
        "equity_curve": [
            [ts.isoformat(), round(eq, 6)]
            for ts, eq in portfolio.equity_curve[-max_points:]
        ],
    }


def load_portfolio_state(portfolio: Portfolio, raw: dict) -> None:
    """Restore a portfolio IN PLACE from portfolio_state_dict output.

    Mutating in place keeps any references (e.g. a paper venue holding the
    same portfolio object) valid after a state reload.
    """
    portfolio.starting_cash = float(raw["starting_cash"])
    portfolio.cash = float(raw["cash"])
    portfolio.positions = {
        sym: Position(
            symbol=sym,
            qty=float(p["qty"]),
            avg_price=float(p["avg_price"]),
            realized_pnl=float(p["realized_pnl"]),
        )
        for sym, p in raw.get("positions", {}).items()
    }
    portfolio.fills = [
        Fill(
            ts=datetime.fromisoformat(f["ts"]),
            symbol=f["symbol"],
            side=Side(f["side"]),
            qty=float(f["qty"]),
            price=float(f["price"]),
            fee=float(f.get("fee", 0.0)),
            realized=float(f.get("realized", 0.0)),
            reason=f.get("reason", ""),
        )
        for f in raw.get("fills", [])
    ]
    portfolio.equity_curve = [
        (datetime.fromisoformat(ts), float(eq))
        for ts, eq in raw.get("equity_curve", [])
    ]
