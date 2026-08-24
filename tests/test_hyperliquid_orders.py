import pytest

from bot.venues.hyperliquid_orders import (
    HyperliquidOrderError,
    HyperliquidOrderVenue,
    _fill_price,
)


def test_missing_key_raises_with_instructions(monkeypatch):
    monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)
    with pytest.raises(HyperliquidOrderError) as excinfo:
        HyperliquidOrderVenue(network="testnet")
    assert "HL_PRIVATE_KEY" in str(excinfo.value)
    assert "faucet" in str(excinfo.value)


def test_fill_price_extracts_nested_avg_px():
    result = {"response": {"data": {"statuses": [{"filled": {"avgPx": "123.45"}}]}}}
    assert _fill_price(result, 100.0) == pytest.approx(123.45)


def test_fill_price_handles_flat_status_shape():
    result = {"statuses": [{"filled": {"avgPx": "77.0"}}]}
    assert _fill_price(result, 100.0) == pytest.approx(77.0)


def test_fill_price_falls_back_when_unfilled():
    result = {"statuses": [{"resting": {"oid": 1}}]}
    assert _fill_price(result, 99.0) == 99.0
    assert _fill_price(None, 55.0) == 55.0
