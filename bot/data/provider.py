"""Turn collected market context into LLM strategy features.

Fetches the live context row, stores it (so history accumulates whenever the
bot runs), and derives features from the stored window: annualized funding,
24h open-interest change, 24h notional volume, mark-vs-oracle premium.
Everything degrades gracefully — missing history just means fewer keys.
"""

from __future__ import annotations

import time

from .hl_collector import HyperliquidContextClient
from .store import MarketDataStore


def derive_features(latest: dict, window: list[dict]) -> dict:
    """Pure feature derivation from a latest context row + ~24h of history."""
    features: dict = {}
    if latest.get("funding") is not None:
        # funding is an hourly rate as a fraction -> annualized percent
        features["funding_ann_pct"] = round(
            latest["funding"] * 24 * 365 * 100, 2
        )
    if latest.get("day_ntl_vlm") is not None:
        features["day_volume_musd"] = round(latest["day_ntl_vlm"] / 1e6, 2)
    if latest.get("premium") is not None:
        features["perp_premium_bps"] = round(latest["premium"] * 1e4, 1)

    ois = [row["open_interest"] for row in window
           if row.get("open_interest") not in (None, 0)]
    if len(ois) >= 2 and ois[0]:
        features["oi_change_24h_pct"] = round((ois[-1] / ois[0] - 1.0) * 100, 2)
    return features


class LiveFeatureProvider:
    """Callable provider for LLMAnalystStrategy: fetch -> store -> derive."""

    def __init__(self, store: MarketDataStore,
                 client: HyperliquidContextClient):
        self.store = store
        self.client = client

    def features(self, coin: str) -> dict:
        now_ms = int(time.time() * 1000)
        contexts = self.client.fetch_contexts()
        ctx = contexts.get(coin)
        if ctx is None:
            return {}
        self.store.insert_context(now_ms, [dict(coin=coin, **ctx)])
        window = self.store.context_window(coin, minutes=24 * 60)
        return derive_features(ctx, window)
