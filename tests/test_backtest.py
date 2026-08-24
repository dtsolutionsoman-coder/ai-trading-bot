import math

from bot.backtest.runner import BacktestConfig, run_backtest
from bot.core.data import generate_sample_bars
from bot.core.models import Side
from bot.strategies.llm_analyst import LLMAnalystStrategy
from bot.strategies.sma_cross import SMACrossStrategy


class FakeClient:
    """Offline stand-in for the LLM client."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat(self, system, user):
        self.calls += 1
        return self.reply


def test_end_to_end_sma_cross():
    bars = generate_sample_bars(600, seed=7)
    result = run_backtest(SMACrossStrategy(10, 40), bars, BacktestConfig())

    assert result.bars_processed == 600
    assert len(result.equity_curve) == 600
    assert len(result.fills) > 0
    assert all(f.fee >= 0 for f in result.fills)

    m = result.metrics
    assert m["final_equity"] > 0
    assert 0.0 <= m["max_drawdown_pct"] < 100.0
    assert math.isfinite(m["total_return_pct"])
    assert m["n_closed_trades"] <= m["n_fills"]


def test_backtest_is_deterministic():
    bars = generate_sample_bars(400, seed=11)
    r1 = run_backtest(SMACrossStrategy(10, 40), bars, BacktestConfig())
    r2 = run_backtest(SMACrossStrategy(10, 40), bars, BacktestConfig())
    assert r1.metrics["final_equity"] == r2.metrics["final_equity"]
    assert len(r1.fills) == len(r2.fills)


def test_llm_hold_never_trades():
    bars = generate_sample_bars(200, seed=3)
    client = FakeClient('{"action":"hold","conviction":0.5,"reason":"nothing convincing"}')
    strat = LLMAnalystStrategy(client=client, every=5, lookback=50)
    result = run_backtest(strat, bars, BacktestConfig())

    assert client.calls > 0  # it did consult the model
    assert result.fills == []
    assert all(d["action"] == "hold" for d in strat.decisions)


def test_llm_buy_opens_long():
    bars = generate_sample_bars(200, seed=3)
    client = FakeClient(
        '```json\n{"action":"buy","conviction":1.0,"reason":"trend is up"}\n```'
    )
    strat = LLMAnalystStrategy(client=client, every=5, lookback=50)
    result = run_backtest(strat, bars, BacktestConfig())

    assert any(f.side is Side.BUY for f in result.fills)
    assert result.metrics["n_fills"] >= 1


def test_llm_garbage_reply_is_safe():
    bars = generate_sample_bars(150, seed=5)
    client = FakeClient("Sorry, I cannot help with that.")
    strat = LLMAnalystStrategy(client=client, every=5, lookback=50)
    result = run_backtest(strat, bars, BacktestConfig())
    assert result.fills == []  # unparseable -> hold, never trade


def test_fees_and_slippage_drag_exists():
    # a chatty strategy that flip-flops every evaluation pays fees
    bars = generate_sample_bars(300, seed=9)
    client = FakeClient('{"action":"buy","conviction":0.9,"reason":"up"}')
    strat = LLMAnalystStrategy(client=client, every=1, lookback=30)
    result = run_backtest(strat, bars, BacktestConfig())
    assert result.metrics["total_fees"] > 0
