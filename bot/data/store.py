"""SQLite time-series store for collected market context (funding, OI, volume).

Uses WAL journaling so the collector can write while the live bot and the
dashboard read. All statements are parameterized — no string-built SQL.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS context (
    ts INTEGER NOT NULL,
    coin TEXT NOT NULL,
    mark_px REAL, mid_px REAL, funding REAL, open_interest REAL,
    day_ntl_vlm REAL, premium REAL
);
CREATE INDEX IF NOT EXISTS idx_context_coin_ts ON context(coin, ts);

CREATE TABLE IF NOT EXISTS funding (
    ts INTEGER NOT NULL,
    coin TEXT NOT NULL,
    rate REAL, premium REAL,
    UNIQUE(coin, ts)
);
"""


class MarketDataStore:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        with self.conn:
            self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ----- writers -----------------------------------------------------------

    def insert_context_rows(self, rows: list[dict]) -> int:
        """rows carry their own 'ts' (used by restore/transfer paths)."""
        with self.conn:
            self.conn.executemany(
                "INSERT INTO context (ts, coin, mark_px, mid_px, funding, "
                "open_interest, day_ntl_vlm, premium) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        int(r["ts"]), r["coin"], r.get("mark_px"), r.get("mid_px"),
                        r.get("funding"), r.get("open_interest"),
                        r.get("day_ntl_vlm"), r.get("premium"),
                    )
                    for r in rows
                ],
            )
        return len(rows)

    def insert_context(self, ts_ms: int, rows: list[dict]) -> int:
        """rows: [{coin, mark_px, mid_px, funding, open_interest,
                   day_ntl_vlm, premium}, ...] stamped with ts_ms."""
        return self.insert_context_rows([dict(r, ts=ts_ms) for r in rows])

    def insert_funding(self, rows: list[dict]) -> int:
        """rows: [{ts, coin, rate, premium}, ...] — idempotent via UNIQUE."""
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO funding (ts, coin, rate, premium) "
                "VALUES (?, ?, ?, ?)",
                [
                    (int(r["ts"]), r["coin"], r.get("rate"), r.get("premium"))
                    for r in rows
                ],
            )
        return len(rows)

    # ----- readers -----------------------------------------------------------

    def context_window(self, coin: str, minutes: int) -> list[dict]:
        cur = self.conn.execute(
            "SELECT ts, mark_px, mid_px, funding, open_interest, day_ntl_vlm, "
            "premium FROM context WHERE coin = ? AND ts >= ? ORDER BY ts",
            (coin, _now_ms() - minutes * 60_000),
        )
        return [dict(zip(_CONTEXT_COLS, row)) for row in cur.fetchall()]

    def latest_context(self, coin: str) -> dict | None:
        cur = self.conn.execute(
            "SELECT ts, mark_px, mid_px, funding, open_interest, day_ntl_vlm, "
            "premium FROM context WHERE coin = ? ORDER BY ts DESC LIMIT 1",
            (coin,),
        )
        row = cur.fetchone()
        return dict(zip(_CONTEXT_COLS, row)) if row else None

    def funding_window(self, coin: str, hours: int) -> list[dict]:
        cur = self.conn.execute(
            "SELECT ts, rate, premium FROM funding WHERE coin = ? AND ts >= ? "
            "ORDER BY ts",
            (coin, _now_ms() - hours * 3_600_000),
        )
        return [dict(zip(_FUNDING_COLS, row)) for row in cur.fetchall()]

    def coins_tracked(self) -> list[str]:
        cur = self.conn.execute("SELECT DISTINCT coin FROM context")
        return [r[0] for r in cur.fetchall()]


_CONTEXT_COLS = ("ts", "mark_px", "mid_px", "funding", "open_interest",
                 "day_ntl_vlm", "premium")
_FUNDING_COLS = ("ts", "rate", "premium")


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)
