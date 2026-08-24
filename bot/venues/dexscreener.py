"""DexScreener public API client (no key) — used by the Solana paper bots.

Verified live (2026-08-24):
  GET /token-profiles/latest/v1 -> recently listed tokens (chainId, tokenAddress)
  GET /latest/dex/tokens/{address} -> pairs[] with priceUsd, liquidity, volume
Endpoints can be slow (~20-45s) — use generous timeouts and poll gently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..core.net import safe_urlopen

_HOST = "api.dexscreener.com"


@dataclass(frozen=True)
class TokenPair:
    pair_address: str
    token_address: str
    symbol: str
    price_usd: float
    liquidity_usd: float
    volume_h24: float
    created_at_ms: int | None  # often None on this endpoint


def parse_pair(raw: dict, token_address: str) -> TokenPair | None:
    try:
        price = float(raw.get("priceUsd") or 0.0)
        if price <= 0.0:
            return None
        liquidity = float(((raw.get("liquidity") or {}).get("usd")) or 0.0)
        volume = float(((raw.get("volume") or {}).get("h24")) or 0.0)
        created = raw.get("pairCreatedAt")
        return TokenPair(
            pair_address=str(raw.get("pairAddress") or token_address),
            token_address=token_address,
            symbol=str((raw.get("baseToken") or {}).get("symbol") or "?")[:20],
            price_usd=price,
            liquidity_usd=liquidity,
            volume_h24=volume,
            created_at_ms=int(created) if created else None,
        )
    except (TypeError, ValueError):
        return None


def best_solana_pair(pairs: list[dict], token_address: str) -> TokenPair | None:
    solana = [p for p in pairs if p.get("chainId") == "solana"]
    best = None
    for raw in solana:
        pair = parse_pair(raw, token_address)
        if pair is None:
            continue
        if best is None or pair.liquidity_usd > best.liquidity_usd:
            best = pair
    return best


class DexScreenerClient:
    def __init__(self, timeout: float = 25.0):
        self.timeout = timeout

    def _get(self, path: str) -> object:
        url = f"https://{_HOST}{path}"
        with safe_urlopen(
            url,
            timeout=self.timeout,
            allowed_hosts={_HOST},
            headers={"User-Agent": "ai-trading-bot/0.1"},
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def latest_solana_tokens(self, limit: int = 30) -> list[str]:
        data = self._get("/token-profiles/latest/v1")
        if not isinstance(data, list):
            return []
        addrs = []
        for row in data:
            if isinstance(row, dict) and row.get("chainId") == "solana":
                addr = row.get("tokenAddress")
                if isinstance(addr, str) and addr:
                    addrs.append(addr)
        return addrs[:limit]

    def token_pair(self, token_address: str) -> TokenPair | None:
        """Best (most liquid) solana pair for a token address."""
        data = self._get(f"/latest/dex/tokens/{token_address}")
        pairs = data.get("pairs") if isinstance(data, dict) else None
        if not isinstance(pairs, list):
            return None
        return best_solana_pair(pairs, token_address)
