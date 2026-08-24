"""Hyperliquid context collector — the seed of your own data API.

Polls the public Info API for per-coin funding, open interest, volume and
premium (`metaAndAssetCtxs`) plus the hourly funding archive
(`fundingHistory`), and stores everything in SQLite. Verified live on
2026-08-24 against the testnet endpoint.

This is deliberately a *polling* collector (simple, restartable). Tick-level
recording via the WebSocket API is the later upgrade on the roadmap.
"""

from __future__ import annotations

import json
import time

from ..core.net import safe_urlopen

_HOSTS = {
    "testnet": "api.hyperliquid-testnet.xyz",
    "mainnet": "api.hyperliquid.xyz",
}


def parse_meta_and_ctxs(payload) -> dict[str, dict]:
    """metaAndAssetCtxs response -> {coin: {mark_px, mid_px, funding,
    open_interest, day_ntl_vlm, premium}} (floats, bad rows skipped)."""
    try:
        meta, ctxs = payload
        universe = meta.get("universe", [])
        out: dict[str, dict] = {}
        for i, coin in enumerate(universe):
            if i >= len(ctxs):
                break
            ctx = ctxs[i]
            name = coin.get("name")
            if not name:
                continue
            try:
                out[name] = {
                    "mark_px": float(ctx["markPx"]),
                    "mid_px": float(ctx.get("midPx") or 0.0),
                    "funding": float(ctx.get("funding") or 0.0),
                    "open_interest": float(ctx.get("openInterest") or 0.0),
                    "day_ntl_vlm": float(ctx.get("dayNtlVlm") or 0.0),
                    "premium": float(ctx.get("premium") or 0.0),
                }
            except (KeyError, TypeError, ValueError):
                continue
        return out
    except (TypeError, ValueError, IndexError):
        return {}


def parse_funding_history(rows, coin: str) -> list[dict]:
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            out.append({
                "ts": int(r["time"]),
                "coin": coin,
                "rate": float(r["fundingRate"]),
                "premium": float(r.get("premium") or 0.0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


class HyperliquidContextClient:
    def __init__(self, network: str = "testnet", timeout: float = 20.0):
        if network not in _HOSTS:
            raise ValueError("network must be testnet or mainnet")
        self.host = _HOSTS[network]
        self.url = f"https://{self.host}/info"
        self.timeout = timeout

    def _post(self, body: dict):
        with safe_urlopen(
            self.url,
            timeout=self.timeout,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "User-Agent": "ai-trading-bot/0.1"},
            allowed_hosts={self.host},
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def fetch_contexts(self) -> dict[str, dict]:
        return parse_meta_and_ctxs(self._post({"type": "metaAndAssetCtxs"}))

    def fetch_funding_history(self, coin: str, hours: int = 72) -> list[dict]:
        now = int(time.time() * 1000)
        rows = self._post({
            "type": "fundingHistory",
            "coin": coin,
            "startTime": now - hours * 3_600_000,
            "endTime": now,
        })
        return parse_funding_history(rows, coin)


class ContextCollector:
    """Fetch -> store, one cycle at a time. Safe to restart; data accumulates."""

    def __init__(self, store, client: HyperliquidContextClient,
                 coins: list[str], collect_funding: bool = True):
        self.store = store
        self.client = client
        self.coins = coins
        self.collect_funding = collect_funding

    def run_once(self) -> dict:
        now_ms = int(time.time() * 1000)
        contexts = self.client.fetch_contexts()
        rows = [dict(coin=c, **contexts[c]) for c in self.coins if c in contexts]
        stored = self.store.insert_context(now_ms, rows)

        funding_rows: list[dict] = []
        if self.collect_funding:
            for coin in self.coins:
                if coin in contexts:
                    funding_rows.extend(self.client.fetch_funding_history(coin))
        self.store.insert_funding(funding_rows)

        return {"coins": [r["coin"] for r in rows], "context_rows": stored,
                "funding_rows": len(funding_rows)}

    def run_forever(self, poll_seconds: float = 60.0) -> None:
        try:
            while True:
                summary = self.run_once()
                print(f"[{time.strftime('%H:%M:%S')}] stored context for "
                      f"{summary['context_rows']} coins, "
                      f"{summary['funding_rows']} funding rows", flush=True)
                time.sleep(max(poll_seconds, 15.0))
        except KeyboardInterrupt:
            print("collector stopped")
