"""Backtest runner: bars -> stops -> strategy -> risk -> simulated fills.

Fill model: execute at the bar close adjusted for slippage (unfavorable side
only) plus fees in bps. Stop-losses always run — even while the daily-loss
breaker is halting new entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from ..core.models import Bar, Fill, Order, costed_fill
from ..core.portfolio import Portfolio
from ..core.risk import RiskConfig, RiskManager
from ..strategies.base import BarContext, Strategy
from .metrics import compute_metrics


@dataclass
class BacktestConfig:
    symbol: str = "BTCUSDT"
    starting_cash: float = 10_000.0
    fee_bps: float = 10.0  # taker fee, basis points of notional
    slippage_bps: float = 5.0  # applied adversely on each fill
    risk: RiskConfig = field(default_factory=RiskConfig)


@dataclass
class BacktestResult:
    config: BacktestConfig
    fills: list[Fill]
    equity_curve: list[tuple]  # (ts, equity)
    metrics: dict
    bars_processed: int
    risk: RiskManager


def run_backtest(
    strategy: Strategy,
    bars: Iterable[Bar],
    config: BacktestConfig,
    progress: Callable[[int], None] | None = None,
) -> BacktestResult:
    portfolio = Portfolio(config.starting_cash)
    risk = RiskManager(config.risk)
    history: list[Bar] = []
    last_day = None

    def simulate_fill(order: Order) -> None:
        """Turn an approved order into a slippage+fee-adjusted paper fill."""
        before = sum(p.realized_pnl for p in portfolio.positions.values())
        fill = costed_fill(order, config.fee_bps, config.slippage_bps)
        portfolio.apply_fill(fill)
        fill.realized = sum(p.realized_pnl for p in portfolio.positions.values()) - before

    processed = 0
    for bar in bars:
        processed += 1
        prices = {config.symbol: bar.close}
        equity = portfolio.equity(prices)

        day = bar.ts.date()
        if day != last_day:
            risk.on_new_day(equity)
            last_day = day
        else:
            risk.check_halt(equity)

        for order in risk.enforce_stops(bar.ts, portfolio.positions, prices):
            simulate_fill(order)

        history.append(bar)
        if not risk.halted:
            ctx = BarContext(
                ts=bar.ts,
                symbol=config.symbol,
                bar=bar,
                history=history,
                portfolio=portfolio,
                equity=equity,
            )
            for order in strategy.on_bar(ctx):
                pos = portfolio.positions.get(order.symbol)
                current_qty = pos.qty if pos else 0.0
                checked = risk.check_order(order, equity, current_qty=current_qty)
                if checked is not None:
                    simulate_fill(checked)

        portfolio.mark(bar.ts, prices)
        if progress is not None and processed % 200 == 0:
            progress(processed)

    metrics = compute_metrics(
        portfolio.equity_curve, portfolio.fills, config.starting_cash
    )
    return BacktestResult(
        config=config,
        fills=portfolio.fills,
        equity_curve=portfolio.equity_curve,
        metrics=metrics,
        bars_processed=processed,
        risk=risk,
    )
