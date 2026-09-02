"""Post-run health gate for CI.

Books whose steps are allowed to soft-fail (e.g. testnet-dependent books
during a CDN block of the runner's IP) keep their last saved_at timestamp.
This module turns that silence into a bounded one: if a tracked book's
state is older than --max-hours, exit nonzero with a GitHub annotation so
the run fails loudly — one alarm per persistent outage, not one per cycle.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

_DEFAULT_BOOKS = ("race_sma.json", "live_llm.json")


def stale_books(paths: list[Path], max_age: timedelta, now: datetime | None = None) -> list[str]:
    """Return names of books whose state file is missing or too old."""
    now = now or datetime.utcnow()
    stale: list[str] = []
    for p in paths:
        if not p.exists():
            stale.append(f"{p.name} (missing)")
            continue
        try:
            saved = datetime.fromisoformat(json.loads(p.read_text(encoding="utf-8"))["saved_at"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            stale.append(f"{p.name} (unreadable)")
            continue
        if now - saved > max_age:
            stale.append(f"{p.name} (saved {saved.isoformat(timespec='minutes')})")
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--books", nargs="*", default=list(_DEFAULT_BOOKS),
                        help="state file names inside output/")
    parser.add_argument("--max-hours", type=float, default=2.0)
    args = parser.parse_args(argv)

    stale = stale_books([Path("output") / b for b in args.books],
                        timedelta(hours=args.max_hours))
    if stale:
        print(f"::error::tracked books stale beyond {args.max_hours}h: "
              + "; ".join(stale))
        return 1
    print(f"health ok: {len(args.books)} tracked book(s) fresh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
