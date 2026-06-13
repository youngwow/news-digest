"""Tests for news_digest.sources.seen_urls.SeenUrlStore."""

import json
from datetime import datetime, timedelta, timezone

from news_digest.sources.seen_urls import SeenUrlStore


def _iso(days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _store(tmp_path, retention_days=7) -> SeenUrlStore:
    return SeenUrlStore(str(tmp_path / "seen_urls.json"), retention_days)


def test_load_returns_empty_when_missing(tmp_path):
    assert _store(tmp_path).load() == {}


def test_load_returns_empty_on_corrupt_json(tmp_path):
    path = tmp_path / "seen_urls.json"
    path.write_text("not valid json{")
    assert SeenUrlStore(str(path), 7).load() == {}


def test_mark_creates_and_appends(tmp_path):
    store = _store(tmp_path)
    store.mark(["https://a.example", "https://b.example"])
    assert set(store.load()) == {"https://a.example", "https://b.example"}
    store.mark(["https://b.example", "https://c.example"])
    assert set(store.load()) == {"https://a.example", "https://b.example", "https://c.example"}


def test_mark_preserves_original_timestamp(tmp_path):
    store = _store(tmp_path)
    store.mark(["https://a.example"])
    first = store.load()["https://a.example"]
    store.mark(["https://a.example", "https://b.example"])
    assert store.load()["https://a.example"] == first


def test_mark_prunes_expired(tmp_path):
    path = tmp_path / "seen_urls.json"
    path.write_text(json.dumps({"https://stale.example": _iso(20),
                                "https://fresh.example": _iso(1)}))
    store = SeenUrlStore(str(path), retention_days=5)
    store.mark(["https://new.example"])
    saved = store.load()
    assert "https://stale.example" not in saved
    assert "https://fresh.example" in saved
    assert "https://new.example" in saved


def test_prune_ignores_malformed_timestamps(tmp_path):
    store = _store(tmp_path, retention_days=5)
    pruned = store._prune({"https://good.example": _iso(1),
                           "https://bad.example": "not-a-date",
                           "https://none.example": None})
    assert pruned == {"https://good.example": pruned["https://good.example"]}
