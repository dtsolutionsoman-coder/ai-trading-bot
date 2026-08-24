from datetime import datetime, timezone

import pytest

from bot.venues.hyperliquid import HyperliquidInfoClient, parse_candles

NOW = 1_787_572_800_000  # fixed "now" for deterministic tests


def row(t, T, o, h, l, c, v="1.0"):
    return {"t": t, "T": T, "s": "BTC", "i": "1h",
            "o": o, "h": h, "l": l, "c": c, "v": v, "n": 10}


def test_parse_keeps_closed_drops_open_candle():
    rows = [
        row(NOW - 7_200_000, NOW - 3_600_001, "100", "110", "95", "105"),
        row(NOW - 3_600_000, NOW - 1, "105", "106", "104", "105.5"),  # just closed
        row(NOW, NOW + 3_599_999, "105.5", "107", "105", "106"),      # still open
    ]
    bars = parse_candles(rows, now_ms=NOW)
    assert len(bars) == 2
    assert bars[-1].close == 105.5


def test_parse_converts_types_and_sorts_ascending():
    rows = [
        row(NOW - 3_600_000, NOW - 1, "105", "106", "104", "105.5"),
        row(NOW - 7_200_000, NOW - 3_600_001, "100", "110", "95", "105"),
    ]
    bars = parse_candles(rows, now_ms=NOW)
    assert [b.close for b in bars] == [105.0, 105.5]
    assert bars[0].ts.tzinfo is None
    assert isinstance(bars[0].volume, float)


def test_parse_empty_rows():
    assert parse_candles([], now_ms=NOW) == []


def test_client_rejects_unknown_network():
    with pytest.raises(ValueError):
        HyperliquidInfoClient(network="devnet")


def test_client_rejects_bad_interval():
    client = HyperliquidInfoClient(network="testnet")
    with pytest.raises(ValueError):
        client.fetch_bars("BTC", "2h", limit=10)


def test_default_now_used_when_omitted():
    bars = parse_candles([row(0, 1, "1", "2", "0.5", "1.5")])  # ancient candle
    assert len(bars) == 1
    assert bars[0].ts.year == 1970
