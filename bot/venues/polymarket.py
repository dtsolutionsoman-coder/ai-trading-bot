"""Polymarket public Gamma API client (read-only market data, no auth).

Verified against the live endpoint (2026-08-24):
  GET https://gamma-api.polymarket.com/markets?closed=false&limit=N
      &order=volume24hr&ascending=false
  -> binary markets with question, outcomes ["Yes","No"],
     outcomePrices ["0.135","0.865"] (strings), volume24hr, liquidity, endDate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..core.net import safe_urlopen

_HOST = "gamma-api.polymarket.com"
_BASE = f"https://{_HOST}/markets"


@dataclass(frozen=True)
class PolMarket:
    id: str
    question: str
    yes_price: float  # 0..1, cost of a YES share
    no_price: float  # 0..1, cost of a NO share
    volume24hr: float
    liquidity: float
    end_date: str
    closed: bool
    active: bool


def _maybe_json_list(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def parse_market(raw: dict) -> PolMarket | None:
    """Convert a Gamma market row; None if not a clean binary market."""
    try:
        outcomes = _maybe_json_list(raw.get("outcomes"))
        prices = _maybe_json_list(raw.get("outcomePrices"))
        if not isinstance(outcomes, list) or not isinstance(prices, list):
            return None
        if len(outcomes) != 2 or len(prices) != 2:
            return None
        if [str(o).strip().lower() for o in outcomes] != ["yes", "no"]:
            return None
        yes = float(prices[0])
        no = float(prices[1])
        if not (0.01 <= yes <= 0.99) or not (0.01 <= no <= 0.99):
            return None  # unpriced or already-resolved extremes
        return PolMarket(
            id=str(raw["id"]),
            question=str(raw.get("question", ""))[:200],
            yes_price=yes,
            no_price=no,
            volume24hr=float(raw.get("volume24hr") or 0.0),
            liquidity=float(raw.get("liquidity") or 0.0),
            end_date=str(raw.get("endDate") or ""),
            closed=bool(raw.get("closed")),
            active=bool(raw.get("active")),
        )
    except (KeyError, TypeError, ValueError):
        return None


class GammaClient:
    def __init__(self, timeout: float = 25.0):
        self.timeout = timeout

    def _fetch(self, query: str) -> list[dict]:
        url = f"{_BASE}?{query}"
        with safe_urlopen(
            url,
            timeout=self.timeout,
            allowed_hosts={_HOST},
            headers={"User-Agent": "ai-trading-bot/0.1"},
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, list) else []

    def top_markets(self, limit: int = 8) -> list[PolMarket]:
        rows = self._fetch(
            f"closed=false&limit={max(limit * 3, 24)}"
            "&order=volume24hr&ascending=false"
        )
        out = []
        for row in rows:
            m = parse_market(row)
            if m is not None and m.active:
                out.append(m)
            if len(out) >= limit:
                break
        return out

    def markets_by_ids(self, ids: list[str]) -> list[PolMarket]:
        if not ids:
            return []
        rows = self._fetch("ids=" + ",".join(str(i) for i in ids))
        out = []
        for row in rows:
            m = parse_market(row)
            if m is None:
                # resolved markets have prices at 1/0 — parse them loosely
                try:
                    outcomes = _maybe_json_list(row.get("outcomes")) or []
                    prices = _maybe_json_list(row.get("outcomePrices")) or []
                    yes = float(prices[0]) if len(prices) == 2 else 0.0
                    no = float(prices[1]) if len(prices) == 2 else 0.0
                    if [str(o).lower() for o in outcomes] == ["yes", "no"]:
                        m = PolMarket(
                            id=str(row["id"]),
                            question=str(row.get("question", ""))[:200],
                            yes_price=max(0.0, min(1.0, yes)),
                            no_price=max(0.0, min(1.0, no)),
                            volume24hr=float(row.get("volume24hr") or 0.0),
                            liquidity=float(row.get("liquidity") or 0.0),
                            end_date=str(row.get("endDate") or ""),
                            closed=bool(row.get("closed")),
                            active=bool(row.get("active")),
                        )
                except (KeyError, TypeError, ValueError, IndexError):
                    m = None
            if m is not None:
                out.append(m)
        return out
