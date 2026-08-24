import json
from datetime import datetime, timedelta

from bot.report import evidence_strength, summarize


def make_state(fills=None, curve=None, decisions=None, saved="2026-08-25T12:00:00"):
    base = datetime(2026, 8, 24, 12, 0)
    curve = curve or [[(base + timedelta(hours=i)).isoformat(), 10_000.0 + i * 10]
                      for i in range(25)]
    return {
        "saved_at": saved,
        "portfolio": {
            "starting_cash": 10_000.0,
            "cash": 10_100.0,
            "positions": {},
            "fills": fills or [],
            "equity_curve": curve,
        },
        "decisions": decisions or {},
    }


def test_summarize_live_book():
    fills = [
        {"ts": "2026-08-24T13:00:00", "symbol": "BTC", "side": "buy",
         "qty": 0.01, "price": 79_000.0, "fee": 1.0, "realized": 0.0,
         "reason": "entry"},
        {"ts": "2026-08-24T18:00:00", "symbol": "BTC", "side": "sell",
         "qty": 0.01, "price": 81_000.0, "fee": 1.0, "realized": 20.0,
         "reason": "take-profit"},
        {"ts": "2026-08-24T20:00:00", "symbol": "BTC", "side": "sell",
         "qty": 0.01, "price": 80_000.0, "fee": 1.0, "realized": -15.0,
         "reason": "stop-loss"},
    ]
    s = summarize(make_state(fills=fills))
    assert s["days"] > 0.9
    assert s["fills"] == 3 and s["closed_trades"] == 2
    assert s["win_rate_pct"] == 50.0
    assert s["return_pct"] > 0
    assert s["decisions"] == 0


def test_summarize_pol_decisions():
    decisions = {
        "1": {"probability": 0.60, "market_price": 0.45},
        "2": {"probability": 0.30, "market_price": 0.50},
        "err": {"error": True},
    }
    s = summarize(make_state(decisions=decisions))
    assert s["decisions"] == 2  # error markers don't count
    assert s["avg_abs_edge"] > 0.14


def test_evidence_strength_thresholds():
    assert "TOO EARLY" in evidence_strength(7)
    assert "WEAK" in evidence_strength(50)
    assert "STATISTICAL" in evidence_strength(150)
