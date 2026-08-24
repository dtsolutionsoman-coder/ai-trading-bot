"""Market data feeds: synthetic generator (offline), CSV cache, public API fetch.

The synthetic feed is the default so every test/demo runs with zero network.
The Binance public klines endpoint needs no API key; results are cached under
data/ so repeat runs are offline too.
"""

from __future__ import annotations

import csv
import json
import math
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Bar
from .net import safe_urlopen

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

_BINANCE_HOST = "api.binance.com"
_BINANCE_BASE = f"https://{_BINANCE_HOST}/api/v3/klines"


class _DeterministicRandom:
    """64-bit LCG + Box-Muller normal sampler.

    Used ONLY to synthesize reproducible fake market data for offline demos
    and tests. Deliberately seeded and deterministic; it is not used for any
    security-relevant purpose.
    """

    _MASK = (1 << 64) - 1
    _MUL = 6364136223846793005
    _ADD = 1442695040888963407

    def __init__(self, seed: int) -> None:
        self._state = (seed & self._MASK) or 1

    def _uniform(self) -> float:
        self._state = (self._state * self._MUL + self._ADD) & self._MASK
        return ((self._state >> 11) & ((1 << 53) - 1)) / float(1 << 53)

    def gauss(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        u1 = self._uniform() or 1e-12
        u2 = self._uniform()
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        return mu + sigma * z

    def uniform(self, a: float, b: float) -> float:
        return a + (b - a) * self._uniform()


def generate_sample_bars(
    n: int = 1000,
    start_price: float = 50_000.0,
    seed: int = 42,
    interval_seconds: int = 3600,
    start: datetime | None = None,
) -> list[Bar]:
    """Random-walk price series with regime shifts so trends/turbulence exist.

    Deterministic for a given seed — backtests are reproducible.
    """
    rng = _DeterministicRandom(seed)
    start = start or datetime(2025, 1, 1)
    bars: list[Bar] = []
    price = start_price
    drift = 0.0
    for i in range(n):
        if i % 120 == 0:  # new drift regime every ~5 days of hourly bars
            drift = rng.gauss(0.0, 0.0015)
        ret = drift + rng.gauss(0.0, 0.008)
        open_ = price
        close = open_ * (1.0 + ret)
        high = max(open_, close) * (1.0 + abs(rng.gauss(0.0, 0.003)))
        low = min(open_, close) * (1.0 - abs(rng.gauss(0.0, 0.003)))
        bars.append(
            Bar(
                ts=start + timedelta(seconds=i * interval_seconds),
                open=round(open_, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=round(rng.uniform(10.0, 1000.0), 2),
            )
        )
        price = close
    return bars


def _utc_from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)


def fetch_binance_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    limit: int = 1000,
    refresh: bool = False,
) -> list[Bar]:
    """Fetch public OHLCV candles and cache them to data/<symbol>_<interval>.csv.

    Locked to the Binance public host; the request goes through the shared
    safe-open helper (scheme/host/IP validation, redirect re-validation).
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache = DATA_DIR / f"{symbol.lower()}_{interval}.csv"
    if cache.exists() and not refresh:
        return load_csv(cache)

    url = f"{_BINANCE_BASE}?symbol={symbol}&interval={interval}&limit={limit}"
    with safe_urlopen(
        url,
        timeout=30.0,
        allowed_hosts={_BINANCE_HOST},
        headers={"User-Agent": "ai-trading-bot/0.1"},
    ) as resp:
        rows = json.loads(resp.read().decode("utf-8"))

    bars = [
        Bar(
            ts=_utc_from_ms(int(r[0])),
            open=float(r[1]),
            high=float(r[2]),
            low=float(r[3]),
            close=float(r[4]),
            volume=float(r[5]),
        )
        for r in rows
    ]
    save_csv(bars, cache)
    return bars


def save_csv(bars: list[Bar], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b.ts.isoformat(), b.open, b.high, b.low, b.close, b.volume])


def load_csv(path: Path) -> list[Bar]:
    with path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [
        Bar(
            ts=datetime.fromisoformat(r["ts"]),
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=float(r["volume"]),
        )
        for r in rows
    ]
