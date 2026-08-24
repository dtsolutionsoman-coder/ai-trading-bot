import json

import pytest

from bot.core.models import Order, Side
from bot.llm.client import LLMError
from bot.pol.runner import (
    PolConfig,
    PolPaperRunner,
    decide_market,
    parse_probability,
)
from bot.venues.polymarket import PolMarket, parse_market


def make_market(mid, yes, closed=False, vol=50_000.0, liq=50_000.0):
    return PolMarket(mid, f"Will event {mid} happen?", yes, round(1 - yes, 3),
                     vol, liq, "2026-12-31T00:00:00Z", closed, True)


class FakeGamma:
    def __init__(self, markets):
        self.markets = markets

    def top_markets(self, limit=8):
        return self.markets[:limit]

    def markets_by_ids(self, ids):
        return [m for m in self.markets if m.id in set(ids)]


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, system, user):
        return self.reply


def make_runner(tmp_path, markets, llm_reply=None, **cfg_overrides):
    cfg = PolConfig(state_path=tmp_path / "pol_state.json", **cfg_overrides)
    llm = FakeLLM(llm_reply) if llm_reply else None
    return PolPaperRunner(FakeGamma(markets), llm, cfg, log=lambda *_: None)


def make_runner_with_llm(tmp_path, llm, markets=None):
    markets = markets if markets is not None else [make_market("1", 0.30)]
    cfg = PolConfig(state_path=tmp_path / "pol_state.json")
    return PolPaperRunner(FakeGamma(markets), llm, cfg, log=lambda *_: None)


# ---------- parsing ----------

def test_parse_market_with_string_encoded_arrays():
    m = parse_market({
        "id": 42, "question": "Q?", "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.30","0.70"]', "volume24hr": 1000.0,
        "liquidity": 500.0, "endDate": "2026-01-01", "active": True,
    })
    assert m is not None
    assert m.yes_price == 0.30 and m.no_price == 0.70 and m.id == "42"


def test_parse_market_rejects_non_binary():
    assert parse_market({
        "id": 1, "question": "Q", "outcomes": '["A","B","C"]',
        "outcomePrices": '["0.3","0.3","0.4"]',
    }) is None


def test_parse_market_rejects_extreme_prices():
    assert parse_market({
        "id": 1, "question": "Q", "outcomes": '["Yes","No"]',
        "outcomePrices": '["1.0","0.0"]',
    }) is None


def test_parse_probability_variants():
    assert parse_probability('{"probability": 0.6, "reason": "r"}')["probability"] == 0.6
    assert parse_probability('```json\n{"probability": 0.6, "reason": "r"}\n```') is not None
    assert parse_probability("I cannot answer") is None
    assert parse_probability('{"probability": 0.0, "reason": "r"}')["probability"] == 0.02
    assert parse_probability('{"probability": 1.0, "reason": "r"}')["probability"] == 0.98


def test_decide_market_wraps_llm_errors():
    class BrokenLLM:
        def chat(self, system, user):
            raise LLMError("boom")

    assert decide_market(BrokenLLM(), make_market("1", 0.5)) is None


def test_transient_failures_are_not_remembered(tmp_path):
    class SometimesBrokenLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, system, user):
            self.calls += 1
            raise LLMError("HTTP 429: rate limited")

    llm = SometimesBrokenLLM()
    runner = make_runner_with_llm(tmp_path, llm)
    runner.run_cycle()
    assert llm.calls >= 1
    assert runner.decisions == {}  # no error markers — retried next cycle
    assert all(p.qty == 0 for p in runner.portfolio.positions.values())


# ---------- runner behavior ----------

def test_buys_yes_when_llm_edge_is_large(tmp_path):
    runner = make_runner(tmp_path, [make_market("1", 0.30)],
                          '{"probability": 0.55, "reason": "likely"}')
    summary = runner.run_cycle()
    assert len(summary["entries"]) == 1
    pos = runner.portfolio.positions["pol:1:Y"]
    assert pos.qty == pytest.approx(100.0 / 0.30, rel=1e-6)
    assert "1" in runner.decisions


def test_buys_no_when_llm_far_below_market(tmp_path):
    runner = make_runner(tmp_path, [make_market("1", 0.70)],
                          '{"probability": 0.50, "reason": "overpriced"}')
    runner.run_cycle()
    pos = runner.portfolio.positions["pol:1:N"]
    assert pos.qty == pytest.approx(100.0 / 0.30, rel=1e-6)


def test_no_entry_below_edge_threshold(tmp_path):
    runner = make_runner(tmp_path, [make_market("1", 0.30)],
                          '{"probability": 0.35, "reason": "meh"}')
    summary = runner.run_cycle()
    assert summary["entries"] == []
    assert all(p.qty == 0 for p in runner.portfolio.positions.values())
    assert "1" in runner.decisions  # decision is remembered even without a trade


def test_take_profit_exit_without_llm(tmp_path):
    runner = make_runner(tmp_path, [make_market("1", 0.50)])
    # open a position manually at 0.20
    runner.venue.submit_order(Order(
        runner.portfolio.equity({}) and __import__("datetime").datetime(2026, 1, 1),
        "pol:1:Y", Side.BUY, 100.0, 0.20, "test entry"))
    summary = runner.run_cycle()  # price now 0.50 -> +0.30 >= take_profit 0.25
    assert any(e["kind"] == "take-profit" for e in summary["exits"])
    assert runner.portfolio.positions["pol:1:Y"].qty == 0


def test_stop_loss_exit(tmp_path):
    runner = make_runner(tmp_path, [make_market("1", 0.20)])
    runner.venue.submit_order(Order(
        __import__("datetime").datetime(2026, 1, 1),
        "pol:1:Y", Side.BUY, 100.0, 0.40, "test entry"))
    summary = runner.run_cycle()  # price 0.20 vs avg 0.40 -> -0.20 <= -0.15
    assert any(e["kind"] == "stop-loss" for e in summary["exits"])


def test_settlement_pays_winner(tmp_path):
    closed = make_market("1", 1.0, closed=True)  # YES won
    runner = make_runner(tmp_path, [closed])
    runner.venue.submit_order(Order(
        __import__("datetime").datetime(2026, 1, 1),
        "pol:1:Y", Side.BUY, 100.0, 0.40, "test entry"))
    summary = runner.run_cycle()
    assert len(summary["settled"]) == 1
    assert runner.portfolio.positions["pol:1:Y"].qty == 0
    # entry fill pays 2% adverse slippage: 100 * 0.40 * 1.02; settlement pays 100 * 1.00
    expected_cash = 10_000.0 - 100 * 0.40 * 1.02 + 100 * 1.0
    assert runner.portfolio.cash == pytest.approx(expected_cash, rel=1e-6)


def test_state_roundtrip(tmp_path):
    runner = make_runner(tmp_path, [make_market("1", 0.30)],
                          '{"probability": 0.55, "reason": "likely"}')
    runner.run_cycle()
    cash = runner.portfolio.cash

    runner2 = make_runner(tmp_path, [make_market("1", 0.30)])
    assert runner2.load_state() is True
    assert runner2.portfolio.cash == cash
    assert "1" in runner2.decisions
    runner2.run_cycle()  # already decided -> no second entry
    assert len(runner2.portfolio.fills) == len(runner.portfolio.fills)


def test_illiquid_markets_skipped(tmp_path):
    thin = make_market("thin", 0.30, vol=100.0, liq=100.0)
    runner = make_runner(tmp_path, [thin],
                          '{"probability": 0.90, "reason": "sure"}')
    summary = runner.run_cycle()
    assert summary["entries"] == []
