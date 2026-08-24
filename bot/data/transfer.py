"""SQLite <-> JSONL transport for the market-data store.

CI runners have no persistent disk: after each run the DB is flattened to
git-friendly JSONL (committed with the state files); the next run restores
the DB from it. Text format diffs cleanly and keeps the repo small.

    python -m bot.data.transfer --export  output/market_data.db  output/market_data.jsonl
    python -m bot.data.transfer --restore output/market_data.jsonl output/market_data.db

Paths are normalized and confined: '..' segments are rejected and every
target must live inside the working directory (or inside
$AITB_TRANSFER_ROOT, which exists so tests can use temp dirs).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .store import MarketDataStore

_CTX_COLS = ("ts", "coin", "mark_px", "mid_px", "funding", "open_interest",
             "day_ntl_vlm", "premium")
_FND_COLS = ("ts", "coin", "rate", "premium")


def _allowed_roots() -> list[Path]:
    roots = [Path.cwd().resolve()]
    extra = os.environ.get("AITB_TRANSFER_ROOT")
    if extra:
        roots.append(Path(extra).resolve())
    return roots


def _checked_path(raw: str | Path) -> Path:
    """Normalize and confine a user-supplied path (no traversal, allowed
    roots only)."""
    candidate = Path(raw)
    if ".." in candidate.parts:
        raise ValueError(f"path traversal ('..') not allowed: {raw}")
    resolved = candidate.resolve()
    roots = _allowed_roots()
    if not any(root == resolved or root in resolved.parents for root in roots):
        raise ValueError(
            f"path outside allowed roots {[str(r) for r in roots]}: {raw}"
        )
    return resolved


def export_store(db_path: str | Path, jsonl_path: str | Path) -> int:
    store = MarketDataStore(_checked_path(db_path))
    out = _checked_path(jsonl_path)
    count = 0
    with out.open("w", encoding="utf-8") as f:
        for row in store.conn.execute(
            "SELECT ts, coin, mark_px, mid_px, funding, open_interest, "
            "day_ntl_vlm, premium FROM context"
        ):
            f.write(json.dumps({"k": "c", "v": list(row)}) + "\n")
            count += 1
        for row in store.conn.execute(
            "SELECT ts, coin, rate, premium FROM funding"
        ):
            f.write(json.dumps({"k": "f", "v": list(row)}) + "\n")
            count += 1
    return count


def restore_store(jsonl_path: str | Path, db_path: str | Path) -> int:
    src = _checked_path(jsonl_path)
    if not src.exists():
        return 0
    store = MarketDataStore(_checked_path(db_path))
    ctx_rows: list[dict] = []
    fnd_rows: list[dict] = []
    with src.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                if rec.get("k") == "c":
                    ctx_rows.append(dict(zip(_CTX_COLS, rec["v"])))
                elif rec.get("k") == "f":
                    fnd_rows.append(dict(zip(_FND_COLS, rec["v"])))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue  # tolerate a torn last line
    store.insert_context_rows(ctx_rows)
    store.insert_funding(fnd_rows)
    return len(ctx_rows) + len(fnd_rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bot.data.transfer")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--export", nargs=2, metavar=("DB", "JSONL"))
    group.add_argument("--restore", nargs=2, metavar=("JSONL", "DB"))
    args = parser.parse_args(argv)

    if args.export:
        n = export_store(args.export[0], args.export[1])
        print(f"exported {n} rows -> {args.export[1]}")
    else:
        n = restore_store(args.restore[0], args.restore[1])
        print(f"restored {n} rows -> {args.restore[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
