import json
from datetime import datetime, timedelta
from pathlib import Path

from bot.ops_health import stale_books


def _write_state(path: Path, saved_at: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"saved_at": saved_at}), encoding="utf-8")


def test_fresh_books_are_not_stale(tmp_path):
    now = datetime.utcnow()
    fresh = tmp_path / "race_sma.json"
    _write_state(fresh, (now - timedelta(minutes=30)).isoformat())
    assert stale_books([fresh], timedelta(hours=2), now=now) == []


def test_old_book_is_stale(tmp_path):
    now = datetime.utcnow()
    old = tmp_path / "live_llm.json"
    _write_state(old, (now - timedelta(hours=3)).isoformat())
    stale = stale_books([old], timedelta(hours=2), now=now)
    assert len(stale) == 1 and "live_llm.json" in stale[0]


def test_missing_and_unreadable_books_are_stale(tmp_path):
    now = datetime.utcnow()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    stale = stale_books([tmp_path / "missing.json", broken],
                        timedelta(hours=2), now=now)
    assert any("missing" in s for s in stale)
    assert any("unreadable" in s for s in stale)
