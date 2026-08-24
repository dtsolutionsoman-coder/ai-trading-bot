from datetime import datetime

from bot.core.models import Fill, Side
from bot.core.portfolio import Portfolio

TS = datetime(2025, 1, 1, 10, 0)


def fill(side, qty, price, fee=0.0):
    return Fill(ts=TS, symbol="BTCUSDT", side=side, qty=qty, price=price, fee=fee)


def test_long_round_trip():
    p = Portfolio(10_000.0)
    p.apply_fill(fill(Side.BUY, 1.0, 100.0))
    assert p.cash == 9_900.0
    pos = p.positions["BTCUSDT"]
    assert pos.qty == 1.0 and pos.avg_price == 100.0

    # marked at 110: 9900 cash + 1 unit * 110 = 10010 (unrealized +10)
    assert p.equity({"BTCUSDT": 110.0}) == 10_010.0

    p.apply_fill(fill(Side.SELL, 1.0, 120.0))
    assert pos.qty == 0.0
    assert pos.realized_pnl == 20.0
    assert p.cash == 10_020.0
    assert p.equity({}) == 10_020.0  # all-cash equity after flat


def test_short_round_trip():
    p = Portfolio(10_000.0)
    p.apply_fill(fill(Side.SELL, 2.0, 100.0))  # short 2 @ 100
    assert p.cash == 10_200.0
    p.apply_fill(fill(Side.BUY, 2.0, 90.0))  # cover @ 90
    pos = p.positions["BTCUSDT"]
    assert pos.qty == 0.0
    assert abs(pos.realized_pnl - 20.0) < 1e-9
    assert abs(p.equity({}) - 10_020.0) < 1e-9


def test_average_entry_price():
    p = Portfolio(10_000.0)
    p.apply_fill(fill(Side.BUY, 1.0, 100.0))
    p.apply_fill(fill(Side.BUY, 1.0, 110.0))
    pos = p.positions["BTCUSDT"]
    assert pos.qty == 2.0
    assert pos.avg_price == 105.0
    assert p.equity({"BTCUSDT": 105.0}) == 10_000.0


def test_flip_long_to_short_with_fees():
    p = Portfolio(10_000.0)
    p.apply_fill(fill(Side.BUY, 1.0, 100.0, fee=1.0))
    p.apply_fill(fill(Side.SELL, 3.0, 110.0, fee=2.0))  # close long, open 2 short

    pos = p.positions["BTCUSDT"]
    assert pos.qty == -2.0
    assert pos.avg_price == 110.0
    assert abs(pos.realized_pnl - 10.0) < 1e-9
    # cash: 10000 -100 -1 +330 -2 = 10227; equity = 10227 - 2*110 = 10007
    assert abs(p.cash - 10_227.0) < 1e-9
    assert abs(p.equity({"BTCUSDT": 110.0}) - 10_007.0) < 1e-9


def test_fees_always_reduce_cash():
    p = Portfolio(10_000.0)
    p.apply_fill(fill(Side.BUY, 1.0, 100.0, fee=0.5))
    p.apply_fill(fill(Side.SELL, 1.0, 100.0, fee=0.5))
    assert abs(p.equity({}) - 9_999.0) < 1e-9  # flat: fees are the only P&L


def test_mark_appends_curve():
    p = Portfolio(10_000.0)
    p.mark(TS, {})
    p.mark(TS, {})
    assert len(p.equity_curve) == 2
