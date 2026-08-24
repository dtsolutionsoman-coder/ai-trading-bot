from datetime import datetime

from bot.core.data import generate_sample_bars
from bot.data.hl_collector import parse_funding_history, parse_meta_and_ctxs
from bot.data.provider import LiveFeatureProvider, derive_features
from bot.data.store import MarketDataStore
from bot.strategies.base import BarContext
from bot.strategies.llm_analyst import LLMAnalystStrategy
from bot.core.portfolio import Portfolio


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []
        self.contexts = {}

    def chat(self, system, user):
        self.calls.append(user)
        return self.reply

    def fetch_contexts(self):
        return self.contexts


CTX_PAYLOAD = [
    {"universe": [{"name": "BTC"}, {"name": "ETH"}, {"name": "BROKEN"}]},
    [
        {"markPx": "79074.0", "midPx": "79108.5", "funding": "0.000177",
         "openInterest": "57.8", "dayNtlVlm": "1714477.0", "premium": "0.00058"},
        {"markPx": "bad", "midPx": "1", "funding": "0", "openInterest": "0",
         "dayNtlVlm": "0", "premium": "0"},
        # BROKEN (index 2) has no ctx entry at all
    ],
]


def test_parse_meta_and_ctxs_skips_bad_rows():
    out = parse_meta_and_ctxs(CTX_PAYLOAD)
    assert set(out) == {"BTC"}  # ETH has bad numbers, BROKEN lacks a ctx
    assert out["BTC"]["funding"] == 0.000177
    assert out["BTC"]["open_interest"] == 57.8
    assert parse_meta_and_ctxs("garbage") == {}


def test_parse_funding_history():
    rows = parse_funding_history(
        [{"time": 1787572800024, "fundingRate": "0.00075", "premium": "0.006"}],
        "BTC",
    )
    assert rows[0]["coin"] == "BTC" and rows[0]["rate"] == 0.00075
    assert parse_funding_history(None, "BTC") == []


def test_store_roundtrip_and_window(tmp_path):
    store = MarketDataStore(tmp_path / "md.db")
    import time

    now = int(time.time() * 1000)
    store.insert_context(now - 3_600_000, [dict(coin="BTC", mark_px=78000.0,
        mid_px=78001.0, funding=0.0001, open_interest=50.0,
        day_ntl_vlm=1_000_000.0, premium=0.0005)])
    store.insert_context(now, [dict(coin="BTC", mark_px=79000.0,
        mid_px=79001.0, funding=0.0002, open_interest=60.0,
        day_ntl_vlm=1_200_000.0, premium=0.0006)])

    window = store.context_window("BTC", minutes=120)
    assert len(window) == 2 and window[0]["open_interest"] == 50.0
    assert store.latest_context("BTC")["mark_px"] == 79000.0

    rows = [{"ts": now, "coin": "BTC", "rate": 0.0007, "premium": 0.0}]
    store.insert_funding(rows)
    store.insert_funding(rows)  # UNIQUE makes this idempotent
    assert len(store.funding_window("BTC", hours=24)) == 1
    assert store.coins_tracked() == ["BTC"]


def test_derive_features_with_and_without_history():
    latest = {"funding": 0.0001, "day_ntl_vlm": 2_000_000.0, "premium": 0.0005,
              "open_interest": 60.0}
    no_history = derive_features(latest, [])
    assert "funding_ann_pct" in no_history
    assert no_history["funding_ann_pct"] == round(0.0001 * 24 * 365 * 100, 2)
    assert "oi_change_24h_pct" not in no_history

    history = [{"open_interest": 50.0}, {"open_interest": 60.0}]
    with_history = derive_features(latest, history)
    assert with_history["oi_change_24h_pct"] == 20.0


def test_provider_stores_and_derives(tmp_path):
    store = MarketDataStore(tmp_path / "md.db")
    client = FakeClient('{"action":"hold","conviction":0,"reason":"x"}')
    client.contexts = {"BTC": {"mark_px": 79074.0, "mid_px": 79108.0,
                               "funding": 0.000177, "open_interest": 57.8,
                               "day_ntl_vlm": 1714477.0, "premium": 0.00058}}
    provider = LiveFeatureProvider(store, client)
    features = provider.features("BTC")
    assert features["funding_ann_pct"] == round(0.000177 * 24 * 365 * 100, 2)
    assert store.latest_context("BTC") is not None  # stored for future windows
    assert provider.features("NOCOIN") == {}


def test_strategy_includes_context_features_in_prompt():
    llm = FakeClient('{"action":"hold","conviction":0.0,"reason":"x"}')
    strat = LLMAnalystStrategy(
        client=llm, every=1, lookback=20,
        context_provider=lambda coin: {"funding_ann_pct": 155.1,
                                       "oi_change_24h_pct": 12.5},
    )
    bars = generate_sample_bars(30, seed=5)
    ctx = BarContext(ts=bars[-1].ts, symbol="BTC", bar=bars[-1],
                     history=bars, portfolio=Portfolio(10_000.0), equity=10_000.0)
    strat.on_bar(ctx)
    assert llm.calls, "strategy should have consulted the LLM"
    assert "funding_ann_pct" in llm.calls[0]
    assert "oi_change_24h_pct" in llm.calls[0]


def test_strategy_survives_broken_context_provider():
    def boom(coin):
        raise RuntimeError("data layer down")

    llm = FakeClient('{"action":"hold","conviction":0.0,"reason":"x"}')
    strat = LLMAnalystStrategy(client=llm, every=1, lookback=20,
                               context_provider=boom)
    bars = generate_sample_bars(30, seed=5)
    ctx = BarContext(ts=bars[-1].ts, symbol="BTC", bar=bars[-1],
                     history=bars, portfolio=Portfolio(10_000.0), equity=10_000.0)
    assert strat.on_bar(ctx) == []  # no crash, no trade
    assert len(llm.calls) == 1
