"""Polymarket paper bot CLI:

    python -m bot.pol.run --once          # one cycle (cron-friendly)
    python -m bot.pol.run --loop          # continuous
Entries need LLM_* env vars; exits/settlement run either way. Paper money only.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ..llm.client import LLMClient, LLMError
from ..venues.polymarket import GammaClient
from .runner import PolConfig, PolPaperRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bot.pol.run")
    parser.add_argument("--once", action="store_true", help="run one cycle then exit")
    parser.add_argument("--loop", action="store_true", help="run continuously")
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--cash", type=float, default=10_000.0)
    parser.add_argument("--cap", type=float, default=100.0,
                        help="max dollars per market")
    parser.add_argument("--edge", type=float, default=0.10,
                        help="min probability edge to enter")
    parser.add_argument("--state", default="output/pol_state.json")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args(argv)

    if not args.once and not args.loop:
        args.once = True  # default to a single safe cycle

    try:
        llm = LLMClient.from_env()
    except LLMError as exc:
        print(f"note: {exc}")
        llm = None

    config = PolConfig(
        starting_cash=args.cash,
        per_market_cap=args.cap,
        edge_threshold=args.edge,
        top_n=args.top,
        state_path=Path(args.state),
    )
    runner = PolPaperRunner(GammaClient(), llm, config)
    if args.reset and config.state_path.exists():
        config.state_path.unlink()
        runner = PolPaperRunner(GammaClient(), llm, config)
        print("state reset — starting flat")

    def report(summary: dict) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] equity ${summary['equity']:,.2f} "
              f"open positions {len(runner._held_market_ids())}")
        for e in summary["entries"]:
            print(f"  ENTRY  {e['instrument']} qty={e['qty']} @ {e['price']:.3f} "
                  f"(edge {e['edge']:+.3f})")
        for e in summary["exits"]:
            print(f"  EXIT   {e['instrument']} @ {e['price']:.3f} ({e['kind']})")
        for s in summary["settled"]:
            print(f"  SETTLE {s['instrument']} @ {s['price']:.3f}")

    if args.once:
        report(runner.run_cycle())
        return 0

    try:
        while True:
            report(runner.run_cycle())
            time.sleep(max(args.poll_seconds, 15.0))
    except KeyboardInterrupt:
        print("interrupted — state saved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
