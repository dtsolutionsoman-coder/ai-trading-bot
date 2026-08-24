"""Live paper-trading CLI:

    python -m bot.live.run --strategy sma_cross --symbol BTC --interval 1h

Polls Hyperliquid (testnet by default) for closed candles and trades them on
the local paper portfolio. State persists across restarts in output/live_state.json.
Execution is paper-only; placing real orders is not implemented anywhere yet.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..core.portfolio import Portfolio
from ..core.risk import RiskConfig
from ..llm.client import LLMClient
from ..strategies.base import Strategy
from ..strategies.llm_analyst import LLMAnalystStrategy
from ..strategies.sma_cross import SMACrossStrategy
from ..venues.hyperliquid import INTERVAL_MS, HyperliquidInfoClient
from ..venues.paper import PaperVenue
from .runner import LiveConfig, LiveRunner


def build_strategy(name: str) -> Strategy:
    if name == "sma_cross":
        return SMACrossStrategy()
    if name == "llm_analyst":
        return LLMAnalystStrategy(client=LLMClient.from_env())
    raise SystemExit(f"unknown strategy {name!r} (choose: sma_cross, llm_analyst)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bot.live.run",
        description="Paper-trade live market data (no real orders, ever).",
    )
    parser.add_argument("--strategy", default="sma_cross",
                        choices=["sma_cross", "llm_analyst", "funding_carry"])
    parser.add_argument("--symbol", default="BTC",
                        help="Hyperliquid coin name (BTC, ETH, SOL, ...)")
    parser.add_argument("--interval", default="1h", choices=sorted(INTERVAL_MS))
    parser.add_argument("--network", default="testnet", choices=["testnet", "mainnet"],
                        help="data source (paper trading either way)")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--cash", type=float, default=10_000.0)
    parser.add_argument("--warmup-bars", type=int, default=500)
    parser.add_argument("--max-bars", type=int, default=None,
                        help="stop after this many new bars (default: run forever)")
    parser.add_argument("--once", action="store_true",
                        help="poll a single cycle then exit (cron-friendly)")
    parser.add_argument("--reset", action="store_true",
                        help="ignore saved state and start flat")
    parser.add_argument("--exec", default="paper", choices=["paper", "testnet"],
                        help="paper: local fills only (default). testnet: REAL "
                             "signed orders on Hyperliquid testnet (fake funds)")
    parser.add_argument("--confirm-live", action="store_true",
                        help="required with --exec testnet: acknowledges real "
                             "testnet orders will be placed")
    parser.add_argument("--context-db", default=None,
                        help="SQLite market-data file (bot.data.run); enriches "
                             "llm_analyst decisions with funding/OI/volume")
    parser.add_argument("--every", type=int, default=6,
                        help="llm_analyst: decide every N bars (default 6)")
    parser.add_argument("--state", default="output/live_state.json")
    args = parser.parse_args(argv)

    venue = None
    if args.exec == "testnet":
        if not args.confirm_live:
            parser.error("--exec testnet also requires --confirm-live")
        from ..venues.hyperliquid_orders import HyperliquidOrderVenue
        venue = HyperliquidOrderVenue(network="testnet")
        print("*** PLACING REAL TESTNET ORDERS (fake funds, real signatures) ***")
        print(f"    wallet: {venue.wallet_address}")

    config = LiveConfig(
        symbol=args.symbol,
        interval=args.interval,
        poll_seconds=args.poll_seconds,
        starting_cash=args.cash,
        risk=RiskConfig(),
        state_path=Path(args.state),
        warmup_bars=args.warmup_bars,
    )

    data_client = HyperliquidInfoClient(network=args.network)
    portfolio = Portfolio(config.starting_cash)
    if venue is None:
        venue = PaperVenue(
            portfolio, fee_bps=config.fee_bps, slippage_bps=config.slippage_bps
        )

    context_provider = None
    if args.context_db:
        from ..data.hl_collector import HyperliquidContextClient
        from ..data.provider import LiveFeatureProvider
        from ..data.store import MarketDataStore

        store = MarketDataStore(Path(args.context_db))
        provider = LiveFeatureProvider(
            store, HyperliquidContextClient(network=args.network)
        )
        context_provider = provider.features
        print(f"decisions enriched with funding/OI/volume from {args.context_db}")
    elif args.strategy == "funding_carry":
        parser.error("funding_carry needs --context-db (it trades the funding rate)")

    if args.strategy == "llm_analyst":
        strategy = LLMAnalystStrategy(
            client=LLMClient.from_env(),
            context_provider=context_provider,
            every=args.every,
        )
    elif args.strategy == "funding_carry":
        from ..strategies.funding_carry import FundingCarryStrategy

        strategy = FundingCarryStrategy(provider=context_provider)
    else:
        strategy = build_strategy(args.strategy)
    runner = LiveRunner(data_client, venue, strategy, config)

    if not args.reset and runner.load_state():
        print(f"resumed saved state from {config.state_path} "
              f"({runner.bars_processed} bars processed so far)")
    else:
        print("starting fresh paper portfolio "
              f"(${config.starting_cash:,.2f}, paper money only)")

    warmed = runner.bootstrap()
    print(f"warmed up with {warmed} closed {args.interval} bars of {args.symbol} "
          f"({args.network}); waiting for the next bar to close")

    if args.once:
        runner.run_one_cycle()
        runner.save_state()
    else:
        runner.run(max_bars=args.max_bars)

    eq = [e for _, e in runner.portfolio.equity_curve]
    if eq:
        print(f"paper equity now ${eq[-1]:,.2f} "
              f"({(eq[-1] / config.starting_cash - 1):+.2%} since start)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
