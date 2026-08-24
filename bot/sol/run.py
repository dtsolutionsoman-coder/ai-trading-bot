"""Solana paper bot CLI:

    python -m bot.sol.run --mode sniper --once
    python -m bot.sol.run --mode copy --wallet <PUBKEY> --once
    python -m bot.sol.run --mode copy --wallet <A> --wallet <B> --loop
Paper money only. Copy mode polls wallet token balances via public RPC —
run it gently (once per minute maximum) to stay under rate limits.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ..venues.dexscreener import DexScreenerClient
from ..venues.solana_rpc import SolanaRpcClient
from .runner import CopyConfig, CopyPaperRunner, SniperConfig, SniperPaperRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bot.sol.run")
    parser.add_argument("--mode", required=True, choices=["sniper", "copy"])
    parser.add_argument("--wallet", action="append", default=[],
                        help="wallet pubkey to copy (repeatable; copy mode)")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=90.0)
    parser.add_argument("--cash", type=float, default=10_000.0)
    parser.add_argument("--cap", type=float, default=50.0,
                        help="max dollars per paper trade")
    parser.add_argument("--state", default=None)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args(argv)

    if not args.once and not args.loop:
        args.once = True

    if args.mode == "sniper":
        config = SniperConfig(
            starting_cash=args.cash, per_token_cap=args.cap,
            state_path=Path(args.state or "output/sol_sniper_state.json"),
        )
        if args.reset and config.state_path.exists():
            config.state_path.unlink()
        runner = SniperPaperRunner(DexScreenerClient(), config)

        def one_cycle() -> None:
            s = runner.run_cycle()
            print(f"[{time.strftime('%H:%M:%S')}] equity ${s['equity']:,.2f} "
                  f"tracked {len(runner.tracked)} skipped {s['skipped']}")
            for o in s["opened"]:
                print(f"  OPEN   {o['symbol']} qty={o['qty']} @ ${o['price']:.10g}")
            for c in s["closed"]:
                print(f"  CLOSE  {c['symbol']} @ ${c['price']:.10g} ({c['reason']})")
    else:
        if not args.wallet:
            parser.error("copy mode needs --wallet <PUBKEY> (repeatable)")
        config = CopyConfig(
            starting_cash=args.cash, per_trade_cap=args.cap,
            state_path=Path(args.state or "output/sol_copy_state.json"),
        )
        if args.reset and config.state_path.exists():
            config.state_path.unlink()
        runner = CopyPaperRunner(SolanaRpcClient(), DexScreenerClient(), config)

        def one_cycle() -> None:
            s = runner.run_cycle(args.wallet)
            print(f"[{time.strftime('%H:%M:%S')}] equity ${s['equity']:,.2f} "
                  f"wallets {len(args.wallet)}")
            for m in s["mirrored"]:
                print(f"  MIRROR {m['side']} {m['instrument']} qty={m['qty']}")

    if args.once:
        one_cycle()
        return 0
    try:
        while True:
            one_cycle()
            time.sleep(max(args.poll_seconds, 60.0))
    except KeyboardInterrupt:
        print("interrupted — state saved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
