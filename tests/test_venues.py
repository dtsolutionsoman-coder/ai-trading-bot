from datetime import datetime

import pytest

from bot.core.models import Order, Side, costed_fill
from bot.core.portfolio import Portfolio
from bot.venues.paper import PaperVenue

TS = datetime(2025, 1, 1, 10, 0)


def order(side, qty, price):
    return Order(ts=TS, symbol="BTC", side=side, qty=qty, price=price)


def test_costed_fill_buy_pays_up_slippage_and_fee():
    f = costed_fill(order(Side.BUY, 1.0, 100.0), fee_bps=10.0, slippage_bps=5.0)
    assert f.price == pytest.approx(100.05)  # buy fills higher
    assert f.fee == pytest.approx(100.05 * 10 / 1e4)


def test_costed_fill_sell_pays_down_slippage():
    f = costed_fill(order(Side.SELL, 1.0, 100.0), fee_bps=10.0, slippage_bps=5.0)
    assert f.price == pytest.approx(99.95)  # sell fills lower
    assert f.fee == pytest.approx(99.95 * 10 / 1e4)


def test_paper_venue_updates_portfolio():
    portfolio = Portfolio(10_000.0)
    venue = PaperVenue(portfolio, fee_bps=10.0, slippage_bps=5.0)
    fill = venue.submit_order(order(Side.BUY, 1.0, 100.0))

    assert fill.price == pytest.approx(100.05)
    pos = portfolio.positions["BTC"]
    assert pos.qty == 1.0
    assert portfolio.fills == [fill]
    assert portfolio.cash == pytest.approx(10_000.0 - 100.05 - fill.fee)


def test_paper_venue_attributes_realized_pnl():
    portfolio = Portfolio(10_000.0)
    venue = PaperVenue(portfolio, fee_bps=10.0, slippage_bps=5.0)
    venue.submit_order(order(Side.BUY, 1.0, 100.0))
    close = venue.submit_order(order(Side.SELL, 1.0, 120.0))
    assert close.realized == pytest.approx(119.94 - 100.05)
