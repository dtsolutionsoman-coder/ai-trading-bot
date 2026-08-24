from datetime import datetime

import pytest

from bot.core.models import Order, Side
from bot.core.portfolio import Portfolio, Position
from bot.core.risk import RiskConfig, RiskManager

TS = datetime(2025, 1, 1, 10, 0)


def order(qty, price, side=Side.BUY):
    return Order(ts=TS, symbol="BTCUSDT", side=side, qty=qty, price=price)


def test_downsizes_oversized_order():
    rm = RiskManager(RiskConfig(max_position_pct=0.25, min_notional_usd=10.0))
    checked = rm.check_order(order(10.0, 100.0), equity=1_000.0)  # notional 1000 > cap 250
    assert checked is not None
    assert checked.qty == pytest.approx(2.5)
    assert "downsized" in checked.reason


def test_drops_dust_orders():
    rm = RiskManager(RiskConfig(min_notional_usd=10.0))
    assert rm.check_order(order(0.05, 100.0), equity=1_000.0) is None  # $5 notional
    assert rm.rejected_orders == 1


def test_passes_orders_within_cap():
    rm = RiskManager(RiskConfig(max_position_pct=0.25, min_notional_usd=10.0))
    checked = rm.check_order(order(2.0, 100.0), equity=1_000.0)  # $200 <= $250
    assert checked is not None and checked.qty == 2.0


def test_cap_blocks_growth_beyond_position_limit():
    rm = RiskManager(RiskConfig(max_position_pct=0.25, min_notional_usd=10.0))
    # already long the full cap: 2.5 units @ 100 = $250 = 25% of $1000 equity
    assert rm.check_order(order(10.0, 100.0), equity=1_000.0, current_qty=2.5) is None
    # but reducing exposure is always allowed
    checked = rm.check_order(
        order(1.0, 100.0, side=Side.SELL), equity=1_000.0, current_qty=2.5
    )
    assert checked is not None and checked.qty == 1.0


def test_cap_trims_order_to_what_fits():
    rm = RiskManager(RiskConfig(max_position_pct=0.25, min_notional_usd=10.0))
    # long 1.0 ($100); buying 10 more ($1000) trims so the result is the 2.5-unit cap
    checked = rm.check_order(order(10.0, 100.0), equity=1_000.0, current_qty=1.0)
    assert checked is not None
    assert checked.qty == pytest.approx(1.5)
    assert "downsized" in checked.reason


def test_stop_loss_triggers_for_long():
    rm = RiskManager(RiskConfig(stop_loss_pct=0.05))
    positions = {"BTCUSDT": Position("BTCUSDT", qty=2.0, avg_price=100.0)}
    stops = rm.enforce_stops(TS, positions, {"BTCUSDT": 94.0})  # -6% < -5%
    assert len(stops) == 1
    assert stops[0].side is Side.SELL and stops[0].qty == 2.0
    assert rm.stop_hits == 1


def test_stop_loss_triggers_for_short():
    rm = RiskManager(RiskConfig(stop_loss_pct=0.05))
    positions = {"BTCUSDT": Position("BTCUSDT", qty=-2.0, avg_price=100.0)}
    stops = rm.enforce_stops(TS, positions, {"BTCUSDT": 106.0})  # +6% against short
    assert len(stops) == 1
    assert stops[0].side is Side.BUY and stops[0].qty == 2.0


def test_no_stop_within_tolerance():
    rm = RiskManager(RiskConfig(stop_loss_pct=0.05))
    positions = {"BTCUSDT": Position("BTCUSDT", qty=2.0, avg_price=100.0)}
    assert rm.enforce_stops(TS, positions, {"BTCUSDT": 95.5}) == []


def test_daily_loss_halt_blocks_entries():
    rm = RiskManager(RiskConfig(daily_loss_limit_pct=0.04))
    rm.on_new_day(1_000.0)
    assert rm.check_halt(950.0) is True  # -5% day trips the breaker
    assert rm.check_order(order(1.0, 100.0), equity=950.0) is None
    assert rm.rejected_orders == 1


def test_halt_resets_next_day():
    rm = RiskManager(RiskConfig(daily_loss_limit_pct=0.04))
    rm.on_new_day(1_000.0)
    rm.check_halt(950.0)
    rm.on_new_day(950.0)  # next session
    assert rm.halted is False


def test_portfolio_unused_import_is_used():
    p = Portfolio(100.0)
    assert p.equity({}) == 100.0
