"""CLI entrypoint: python -m bot.backtest.run --help"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from ..core import data
from ..core.risk import RiskConfig
from ..llm.client import LLMClient
from ..strategies.base import Strategy
from ..strategies.llm_analyst import LLMAnalystStrategy
from ..strategies.sma_cross import SMACrossStrategy
from .runner import BacktestConfig, run_backtest

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output"


def build_strategy(name: str) -> Strategy:
    if name == "sma_cross":
        return SMACrossStrategy()
    if name == "llm_analyst":
        return LLMAnalystStrategy(client=LLMClient.from_env())
    raise SystemExit(f"unknown strategy {name!r} (choose: sma_cross, llm_analyst)")


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def print_report(strategy_name: str, result) -> None:
    m = result.metrics
    cfg = result.config
    print("=" * 62)
    print("BACKTEST REPORT")
    print("=" * 62)
    print(f"strategy         {strategy_name}")
    print(f"symbol / bars    {cfg.symbol} / {result.bars_processed}")
    print(f"risk caps        pos<= {cfg.risk.max_position_pct:.0%} equity, "
          f"stop {cfg.risk.stop_loss_pct:.0%}, "
          f"daily halt {cfg.risk.daily_loss_limit_pct:.0%}")
    print("-" * 62)
    print(f"final equity     {_fmt_money(m['final_equity'])} "
          f"(started {_fmt_money(m['starting_cash'])})")
    print(f"total return     {_fmt_pct(m['total_return_pct'])}")
    print(f"ann. return*     {_fmt_pct(m['annualized_return_pct'])}")
    print(f"sharpe*          {m['sharpe']:.2f}")
    print(f"max drawdown     -{m['max_drawdown_pct']:.2f}%")
    print(f"fills / closed   {m['n_fills']} / {m['n_closed_trades']}")
    print(f"win rate         {m['win_rate_pct']:.1f}%")
    pf = m["profit_factor"]
    print(f"profit factor    {'n/a' if pf is None else f'{pf:.2f}'}")
    print(f"fees paid        {_fmt_money(m['total_fees'])}")
    print(f"stop-loss hits   {result.risk.stop_hits}")
    print(f"orders blocked   {result.risk.rejected_orders} (halt/dust)")
    print("-" * 62)
    print("* naive constant-interval annualization; synthetic data is not a"
          " forecast of anything")
    if result.fills:
        print("\nlast 5 fills:")
        for f in result.fills[-5:]:
            print(f"  {f.ts}  {f.side.value:<4} {f.qty:>10.6f} @ "
                  f"{f.price:>12.2f}  realized {f.realized:>9.2f}  {f.reason[:60]}")
    print("=" * 62)


def write_outputs(strategy_name: str, result) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = OUTPUT_DIR / f"backtest_{strategy_name}_{datetime.now():%Y%m%d_%H%M%S}"

    eq_path = stem.with_suffix(".csv")
    with eq_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "equity"])
        for ts, eq in result.equity_curve:
            w.writerow([ts.isoformat(), f"{eq:.2f}"])

    summary_path = stem.with_name(stem.name + "_summary.json")
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "strategy": strategy_name,
                "bars_processed": result.bars_processed,
                "metrics": result.metrics,
                "fills": [
                    {
                        "ts": fl.ts.isoformat(),
                        "side": fl.side.value,
                        "qty": fl.qty,
                        "price": fl.price,
                        "fee": fl.fee,
                        "realized": fl.realized,
                        "reason": fl.reason,
                    }
                    for fl in result.fills
                ],
            },
            f,
            indent=2,
            default=str,
        )
    return eq_path, summary_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bot.backtest.run",
        description="Run a backtest (paper money only — nothing here touches real funds).",
    )
    parser.add_argument("--strategy", default="sma_cross",
                        choices=["sma_cross", "llm_analyst"])
    parser.add_argument("--source", default="sample", choices=["sample", "binance"])
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h",
                        help="bar interval for the binance source (1h, 4h, 1d...)")
    parser.add_argument("--bars", type=int, default=1000)
    parser.add_argument("--cash", type=float, default=10_000.0)
    parser.add_argument("--seed", type=int, default=42, help="seed for sample data")
    parser.add_argument("--refresh", action="store_true",
                        help="re-download even if a cached CSV exists")
    args = parser.parse_args(argv)

    if args.source == "sample":
        bars = data.generate_sample_bars(n=args.bars, seed=args.seed)
    else:
        try:
            bars = data.fetch_binance_klines(
                symbol=args.symbol,
                interval=args.interval,
                limit=min(args.bars, 1000),
                refresh=args.refresh,
            )
        except Exception as exc:  # network/geo issues must not crash the demo
            print(f"could not fetch market data: {exc}\n"
                  f"falling back? no — rerun with --source sample for an offline demo.",
                  file=sys.stderr)
            return 2

    strategy = build_strategy(args.strategy)
    config = BacktestConfig(
        symbol=args.symbol if args.source == "binance" else "SAMPLEUSDT",
        starting_cash=args.cash,
        risk=RiskConfig(),
    )
    result = run_backtest(strategy, bars, config)
    print_report(args.strategy, result)
    eq_path, sum_path = write_outputs(args.strategy, result)
    print(f"\nwrote {eq_path}")
    print(f"wrote {sum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
