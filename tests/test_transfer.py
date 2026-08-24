import pytest

from bot.data.store import MarketDataStore
from bot.data.transfer import _checked_path, export_store, restore_store


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    monkeypatch.setenv("AITB_TRANSFER_ROOT", str(tmp_path))
    return tmp_path


def test_roundtrip_preserves_everything(roots):
    import time

    now = int(time.time() * 1000)
    db = roots / "md.db"
    store = MarketDataStore(db)
    store.insert_context(now - 2_000, [dict(coin="BTC", mark_px=100.0, mid_px=100.1,
                                            funding=0.0001, open_interest=5.0,
                                            day_ntl_vlm=1000.0, premium=0.0001)])
    store.insert_context(now - 1_000, [dict(coin="ETH", mark_px=50.0, mid_px=50.0,
                                            funding=0.0, open_interest=1.0,
                                            day_ntl_vlm=10.0, premium=0.0)])
    store.insert_funding([{"ts": now - 1_500, "coin": "BTC", "rate": 0.0001,
                           "premium": 0.0}])
    store.close()

    jsonl = roots / "md.jsonl"
    assert export_store(db, jsonl) == 3

    db2 = roots / "restored.db"
    assert restore_store(jsonl, db2) == 3
    restored = MarketDataStore(db2)
    btc = restored.latest_context("BTC")
    assert btc["ts"] == now - 2_000 and btc["mark_px"] == 100.0
    assert len(restored.funding_window("BTC", hours=24)) == 1


def test_restore_missing_file_is_noop(roots):
    assert restore_store(roots / "nope.jsonl", roots / "fresh.db") == 0


def test_restore_tolerates_torn_line(roots):
    jsonl = roots / "torn.jsonl"
    jsonl.write_text(
        '{"k": "f", "v": [1500, "BTC", 0.0001, 0.0]}\n'
        '{"k": "c", "v": [1000, "BTC", 100.0, 100.0, 0.0001, 5.0, 1000.0, 0.0]}\n'
        '{"k": "c", "v": [TRUNCATED',
        encoding="utf-8",
    )
    db = roots / "torn.db"
    assert restore_store(jsonl, db) == 2  # good rows restored, torn line skipped


def test_checked_path_rejects_traversal(roots):
    with pytest.raises(ValueError):
        _checked_path("../escape.jsonl")
    with pytest.raises(ValueError):
        _checked_path("output/../../etc/passwd")
    # inside the allowed root is fine
    assert _checked_path(roots / "ok.jsonl") == (roots / "ok.jsonl").resolve()


def test_checked_path_rejects_outside_roots(tmp_path, roots):
    other = tmp_path / "elsewhere.jsonl"  # tmp_path IS the root; make sibling
    outside = roots.parent / "outside.jsonl"
    with pytest.raises(ValueError):
        _checked_path(outside)
