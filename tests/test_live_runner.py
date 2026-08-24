import json

from bot.core.data import generate_sample_bars
from bot.core.models import Side
from bot.core.portfolio import Portfolio
from bot.core.risk import RiskConfig
from bot.live.runner import LiveConfig, LiveRunner
from bot.strategies.llm_analyst import LLMAnalystStrategy
from bot.venues.paper import PaperVenue


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat(self, system, user):
        self.calls += 1
        return self.reply


class FakeDataClient:
    """Each fetch_bars() call returns the next snapshot (last one repeats)."""

    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = 0

    def fetch_bars(self, symbol, interval, limit=500):
        self.calls += 1
        idx = min(self.calls - 1, len(self.snapshots) - 1)
        return self.snapshots[idx][-limit:]


def make_runner(tmp_path, snapshots, llm_reply, start_cash=10_000.0):
    data = FakeDataClient(snapshots)
    portfolio = Portfolio(start_cash)
    venue = PaperVenue(portfolio, fee_bps=10.0, slippage_bps=5.0)
    llm = FakeClient(llm_reply)
    strategy = LLMAnalystStrategy(client=llm, every=1, lookback=20)
    config = LiveConfig(
        symbol="BTC", interval="1h", poll_seconds=0.0,
        starting_cash=start_cash, risk=RiskConfig(),
        state_path=tmp_path / "live_state.json", warmup_bars=500,
    )
    runner = LiveRunner(data, venue, strategy, config, log=lambda *_: None)
    return runner, llm


def test_bootstrap_warms_up_without_trading(tmp_path):
    bars = generate_sample_bars(60, seed=1)
    runner, llm = make_runner(tmp_path, [bars], '{"action":"buy","conviction":1.0,"reason":"x"}')
    warmed = runner.bootstrap()
    assert warmed == 60
    assert llm.calls == 0  # warmup never consults the strategy
    assert runner.portfolio.fills == []


def test_new_bar_trades_and_saves_state(tmp_path):
    bars = generate_sample_bars(60, seed=1)
    next_bar = generate_sample_bars(61, seed=1)[-1]
    runner, llm = make_runner(tmp_path, [bars, bars + [next_bar]],
                              '{"action":"buy","conviction":1.0,"reason":"trend"}')
    runner.bootstrap()
    processed = runner.run_one_cycle()

    assert processed == 1
    assert llm.calls == 1
    assert any(f.side is Side.BUY for f in runner.portfolio.fills)
    assert runner.config.state_path.exists()

    state = json.loads(runner.config.state_path.read_text())
    assert state["last_bar_ts"] == next_bar.ts.isoformat()
    assert state["bars_processed"] == 1


def test_resume_does_not_retrade_old_bars(tmp_path):
    bars = generate_sample_bars(60, seed=1)
    next_bar = generate_sample_bars(61, seed=1)[-1]
    full = bars + [next_bar]
    runner, llm = make_runner(tmp_path, [bars, full],
                              '{"action":"buy","conviction":1.0,"reason":"trend"}')
    runner.bootstrap()
    runner.run_one_cycle()
    cash_after_first = runner.portfolio.cash

    # fresh runner resuming from the saved state sees the same snapshot but
    # must not process the already-traded bar again
    portfolio2 = Portfolio(10_000.0)
    venue2 = PaperVenue(portfolio2, fee_bps=10.0, slippage_bps=5.0)
    llm2 = FakeClient('{"action":"buy","conviction":1.0,"reason":"trend"}')
    strategy2 = LLMAnalystStrategy(client=llm2, every=1, lookback=20)
    runner2 = LiveRunner(
        FakeDataClient([full]), venue2, strategy2, runner.config, log=lambda *_: None
    )
    assert runner2.load_state() is True
    runner2.bootstrap()  # dedupes bars <= last_bar_ts
    assert runner2.run_one_cycle() == 0
    assert llm2.calls == 0
    assert portfolio2.cash == cash_after_first
    assert len(portfolio2.fills) == len(runner.portfolio.fills)


def test_run_stops_at_max_bars(tmp_path):
    bars = generate_sample_bars(60, seed=2)
    grown = generate_sample_bars(62, seed=2)
    runner, _llm = make_runner(tmp_path, [bars, grown],
                               '{"action":"hold","conviction":0.0,"reason":"x"}')
    runner.bootstrap()
    processed = runner.run(max_bars=2)
    assert processed == 2
    assert runner.bars_processed == 2


def test_corrupt_state_recovered_not_fatal(tmp_path):
    bars = generate_sample_bars(60, seed=8)
    runner, _llm = make_runner(tmp_path, [bars],
                               '{"action":"hold","conviction":0.0,"reason":"x"}')
    cfg = runner.config
    cfg.state_path.write_text('{"portfolio": {"cash": 12', encoding="utf-8")  # torn JSON

    fresh, _llm2 = make_runner(tmp_path, [bars],
                               '{"action":"hold","conviction":0.0,"reason":"x"}')
    fresh.config.state_path = cfg.state_path  # same file
    assert fresh.load_state() is False            # starts fresh, no crash
    assert fresh.portfolio.equity({}) == 10_000.0
    corrupt_copies = list(cfg.state_path.parent.glob("*.corrupt-*"))
    assert len(corrupt_copies) == 1               # evidence preserved for debug


def test_decisions_and_usage_persist_across_restart(tmp_path):
    bars = generate_sample_bars(60, seed=4)
    next_bar = generate_sample_bars(61, seed=4)[-1]
    runner, llm = make_runner(tmp_path, [bars, bars + [next_bar]],
                              '{"action":"hold","conviction":0.5,"reason":"calm"}')
    llm.usage = {"calls": 3, "prompt_tokens": 600, "completion_tokens": 300}
    runner.bootstrap()
    runner.run_one_cycle()  # one hold decision gets logged
    assert runner.strategy.decisions, "decision should have been recorded"

    runner2, llm2 = make_runner(tmp_path, [bars + [next_bar]],
                                '{"action":"hold","conviction":0.5,"reason":"calm"}')
    llm2.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    assert runner2.load_state() is True
    assert runner2.strategy.decisions == runner.strategy.decisions
    assert llm2.usage["calls"] == 3
    assert llm2.usage["prompt_tokens"] == 600


def test_reset_ignores_existing_state(tmp_path):
    bars = generate_sample_bars(60, seed=3)
    next_bar = generate_sample_bars(61, seed=3)[-1]
    runner, _ = make_runner(tmp_path, [bars, bars + [next_bar]],
                            '{"action":"buy","conviction":1.0,"reason":"x"}')
    runner.bootstrap()
    runner.run_one_cycle()

    fresh, _llm = make_runner(tmp_path, [bars + [next_bar]],
                              '{"action":"hold","conviction":0.0,"reason":"x"}')
    assert fresh.state_file_exists()
    assert fresh.load_state() is True  # explicit load still works
    fresh.portfolio = Portfolio(10_000.0)  # what --reset does: start flat
    fresh.last_bar_ts = None
    assert fresh.portfolio.equity({}) == 10_000.0
