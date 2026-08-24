"""Data collector CLI:

    python -m bot.data.run --coins BTC,ETH,SOL --once     # single cycle
    python -m bot.data.run --coins BTC,ETH,SOL --loop     # 24/7 collector
Runs forever building your local market-data history in SQLite.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .hl_collector import ContextCollector, HyperliquidContextClient
from .store import MarketDataStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bot.data.run")
    parser.add_argument("--coins", default="BTC,ETH,SOL")
    parser.add_argument("--db", default="output/market_data.db")
    parser.add_argument("--network", default="testnet",
                        choices=["testnet", "mainnet"])
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--no-funding", action="store_true",
                        help="skip funding-history backfill")
    args = parser.parse_args(argv)

    if not args.once and not args.loop:
        args.once = True

    coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    store = MarketDataStore(Path(args.db))
    client = HyperliquidContextClient(network=args.network)
    collector = ContextCollector(
        store, client, coins, collect_funding=not args.no_funding
    )

    if args.once:
        summary = collector.run_once()
        print(f"stored context for {summary['context_rows']} coins "
              f"({', '.join(summary['coins'])}), "
              f"{summary['funding_rows']} funding-history rows -> {args.db}")
        tracked = store.context_window(coins[0], minutes=24 * 60)
        print(f"{coins[0]} rows in last 24h window: {len(tracked)}")
        return 0

    print(f"collector running: {len(coins)} coins every "
          f"{max(args.poll_seconds, 15):.0f}s -> {args.db} (Ctrl+C to stop)")
    collector.run_forever(poll_seconds=args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
