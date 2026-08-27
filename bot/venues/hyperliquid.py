"""Hyperliquid public Info API client (market data only, no authentication).

Verified against the live testnet endpoint (2026-08-24):
  POST https://api.hyperliquid-testnet.xyz/info
       {"type": "candleSnapshot",
        "req": {"coin": "BTC", "interval": "1h",
                "startTime": <ms>, "endTime": <ms>}}
  -> ascending candle list; strings for o/h/l/c/v; t = open ms, T = close ms;
     the final candle is the still-open one (T >= now) and is dropped here.

Order execution is NOT implemented yet: placing orders requires an
EIP-712-signed wallet action and is deliberately deferred (phase 2b).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from ..core.models import Bar
from ..core.net import safe_urlopen

_NETWORKS = {
    "testnet": "api.hyperliquid-testnet.xyz",
    "mainnet": "api.hyperliquid.xyz",
}

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def _utc_from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)


def parse_candles(rows: list[dict], now_ms: int | None = None) -> list[Bar]:
    """Convert raw candleSnapshot rows to Bars, dropping still-open candles.

    Pure function — unit-tested offline against the documented payload shape.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    bars = [
        Bar(
            ts=_utc_from_ms(int(r["t"])),
            open=float(r["o"]),
            high=float(r["h"]),
            low=float(r["l"]),
            close=float(r["c"]),
            volume=float(r["v"]),
        )
        for r in rows
        if int(r.get("T", 0)) < now_ms  # keep only fully-closed candles
    ]
    bars.sort(key=lambda b: b.ts)
    return bars


class HyperliquidInfoClient:
    """Public market data from Hyperliquid (testnet by default)."""

    def __init__(self, network: str = "testnet", timeout: float = 20.0):
        if network not in _NETWORKS:
            raise ValueError(f"unknown network {network!r}; choose: testnet, mainnet")
        self.network = network
        self.host = _NETWORKS[network]
        self.url = f"https://{self.host}/info"
        self.timeout = timeout

    def fetch_bars(self, symbol: str, interval: str, limit: int = 500) -> list[Bar]:
        if interval not in INTERVAL_MS:
            raise ValueError(
                f"unsupported interval {interval!r}; choose: {sorted(INTERVAL_MS)}"
            )
        limit = max(1, min(limit, 5000))
        step = INTERVAL_MS[interval]
        now_ms = int(time.time() * 1000)
        body = json.dumps(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": symbol,
                    "interval": interval,
                    "startTime": now_ms - (limit + 2) * step,
                    "endTime": now_ms,
                },
            }
        ).encode("utf-8")

        with safe_urlopen(
            self.url,
            timeout=self.timeout,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "ai-trading-bot/0.1",
            },
            allowed_hosts={self.host},
            retries=2,
        ) as resp:
            rows = json.loads(resp.read().decode("utf-8"))

        bars = parse_candles(rows, now_ms)
        return bars[-limit:]
