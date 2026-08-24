from datetime import datetime

import pytest

from bot.core.data import generate_sample_bars
from bot.core.models import Bar, Fill, Side
from bot.core.portfolio import Portfolio
from bot.strategies.base import BarContext
from bot.strategies.funding_carry import FundingCarryStrategy
from bot.strategies.llm_analyst import LLMAnalystStrategy
from bot.pol.report import score_decisions


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, system, user):
        self.calls.append(user)
        return self.reply


def make_ctx(price=100.0, portfolio=None):
    ts = datetime(2025, 1, 1, 12, 0)
    bar = Bar(ts, price, price, price, price, 1.0)
    portfolio = portfolio or Portfolio(10_000.0)
    return BarContext(ts=ts, symbol="BTC", bar=bar, history=[bar],
                      portfolio=portfolio,
                      equity=portfolio.equity({"BTC": price}))


# ---------- LLM position awareness ----------

def test_llm_prompt_includes_open_position():
    llm = FakeLLM('{"action":"hold","conviction":0.0,"reason":"x"}')
    strat = LLMAnalystStrategy(client=llm, every=1, lookback=5)
    portfolio = Portfolio(10_000.0)
    portfolio.apply_fill(Fill(datetime(2025, 1, 1), "BTC", Side.BUY, 1.0, 100.0))

    bars = generate_sample_bars(30, seed=5)
    ctx = BarContext(ts=bars[-1].ts, symbol="BTC", bar=bars[-1],
                     history=bars, portfolio=portfolio,
                     equity=portfolio.equity({"BTC": bars[-1].close}))
    strat.on_bar(ctx)

    assert "current_position" in llm.calls[0]
    assert "unrealized_pnl_pct" in llm.calls[0]


def test_llm_prompt_flat_has_no_position_keys():
    llm = FakeLLM('{"action":"hold","conviction":0.0,"reason":"x"}')
    strat = LLMAnalystStrategy(client=llm, every=1, lookback=5)
    bars = generate_sample_bars(30, seed=5)
    ctx = BarContext(ts=bars[-1].ts, symbol="BTC", bar=bars[-1],
                     history=bars, portfolio=Portfolio(10_000.0), equity=10_000.0)
    strat.on_bar(ctx)
    assert "current_position" not in llm.calls[0]


# ---------- interval-aware feature windows ----------

def _features_from_prompt(prompt):
    import json as _json
    payload = prompt.split("Market features JSON:\n", 1)[1].split("\n\n", 1)[0]
    return _json.loads(payload)


def test_windows_scale_with_bars_per_hour():
    s = LLMAnalystStrategy(client=None, every=1, bars_per_hour=4.0)
    assert s.n_1h == 4 and s.n_6h == 24 and s.n_24h == 96
    assert s.sma_fast == 48 and s.sma_slow == 192  # true 12h / 48h SMAs
    assert s.lookback >= 192

    s1h = LLMAnalystStrategy(client=None, every=1)  # 1h bars, default
    assert s1h.n_24h == 24 and s1h.sma_slow == 48


def test_chg_24h_means_24_hours_on_15m_bars():
    # flat at 100; 96 bars ago price was 105; now 110
    # -> true 24h change = 110/105-1 = +4.76% (NOT the 6h +10%)
    closes = [100.0] * 200
    closes[-97] = 105.0
    closes[-1] = 110.0
    bars = [
        Bar(datetime(2025, 1, 1, 0, i * 15 // 60, (i * 15) % 60),
            c, c, c, c, 1.0)
        for i, c in enumerate(closes)
    ]
    llm = FakeLLM('{"action":"hold","conviction":0.0,"reason":"x"}')
    strat = LLMAnalystStrategy(client=llm, every=1, bars_per_hour=4.0)
    ctx = BarContext(ts=bars[-1].ts, symbol="BTC", bar=bars[-1],
                     history=bars, portfolio=Portfolio(10_000.0), equity=10_000.0)
    strat.on_bar(ctx)
    features = _features_from_prompt(llm.calls[0])
    assert features["chg_24h_pct"] == pytest.approx(
        (110.0 / 105.0 - 1) * 100, abs=0.01
    )
    assert "sma12h_sma48h_ratio" in features


# ---------- funding carry ----------

def test_carry_shorts_extreme_positive_funding():
    strat = FundingCarryStrategy(provider=lambda c: {"funding_ann_pct": 120.0},
                                 position_frac=0.5)
    orders = strat.on_bar(make_ctx())
    assert len(orders) == 1
    assert orders[0].side is Side.SELL
    assert orders[0].qty == pytest.approx(0.5 * 10_000 / 100.0)
    assert "short" in orders[0].reason


def test_carry_longs_extreme_negative_funding():
    strat = FundingCarryStrategy(provider=lambda c: {"funding_ann_pct": -90.0},
                                 position_frac=0.5)
    orders = strat.on_bar(make_ctx())
    assert orders[0].side is Side.BUY
    assert "long" in orders[0].reason


def test_carry_goes_flat_when_funding_decays():
    portfolio = Portfolio(10_000.0)
    portfolio.apply_fill(Fill(datetime(2025, 1, 1), "BTC", Side.SELL, 50.0, 100.0))
    strat = FundingCarryStrategy(provider=lambda c: {"funding_ann_pct": 4.0},
                                 position_frac=0.5)
    orders = strat.on_bar(make_ctx(portfolio=portfolio))
    assert len(orders) == 1
    assert orders[0].side is Side.BUY  # closing the short
    assert "flat" in orders[0].reason


def test_carry_holds_stance_inside_band():
    portfolio = Portfolio(10_000.0)
    portfolio.apply_fill(Fill(datetime(2025, 1, 1), "BTC", Side.SELL, 50.0, 100.0))
    strat = FundingCarryStrategy(provider=lambda c: {"funding_ann_pct": 30.0},
                                 position_frac=0.5)
    assert strat.on_bar(make_ctx(portfolio=portfolio)) == []


def test_carry_without_data_does_nothing():
    strat = FundingCarryStrategy(provider=lambda c: {})
    assert strat.on_bar(make_ctx()) == []


def test_carry_rejects_bad_thresholds():
    with pytest.raises(ValueError):
        FundingCarryStrategy(provider=lambda c: {}, entry_ann_pct=5.0,
                             exit_ann_pct=10.0)


def test_carry_evaluates_every_n_bars():
    calls = {"n": 0}

    def provider(coin):
        calls["n"] += 1
        return {"funding_ann_pct": 120.0}

    strat = FundingCarryStrategy(provider=provider, every=3)
    assert strat.on_bar(make_ctx()) == []      # bar 1: skipped
    assert strat.on_bar(make_ctx()) == []      # bar 2: skipped
    assert len(strat.on_bar(make_ctx())) == 1  # bar 3: decided
    assert calls["n"] == 1


# ---------- calibration scoring ----------

def test_score_decisions_model_beats_market():
    decisions = {
        "a": {"probability": 0.95, "market_price": 0.50},
        "b": {"probability": 0.10, "market_price": 0.50},
    }
    finals = {"a": 1.0, "b": 0.0}
    score = score_decisions(decisions, finals)
    assert score["settled"] == 2
    assert score["model_beats_market"] is True
    assert score["model_closer_count"] == 2


def test_score_decisions_ignores_unresolved():
    decisions = {"a": {"probability": 0.9, "market_price": 0.5},
                 "open": {"probability": 0.6, "market_price": 0.5}}
    score = score_decisions(decisions, {"a": 1.0, "open": 0.55})
    assert score["settled"] == 1


def test_score_decisions_empty():
    assert score_decisions({}, {}) == {"settled": 0}
