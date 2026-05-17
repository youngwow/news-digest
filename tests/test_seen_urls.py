"""Tests for the seen-URL retention helpers in utils.py."""

import json
from datetime import datetime, timedelta, timezone

import utils
from utils import _prune_seen_urls, load_seen_urls, mark_urls_seen


def _iso(days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_prune_keeps_recent_drops_old():
    urls = {
        "https://recent.example/a": _iso(1),
        "https://old.example/b":    _iso(10),
        "https://edge.example/c":   _iso(3),
    }
    pruned = _prune_seen_urls(urls, retention_days=5)
    assert "https://recent.example/a" in pruned
    assert "https://edge.example/c" in pruned
    assert "https://old.example/b" not in pruned


def test_prune_ignores_malformed_timestamps():
    urls = {
        "https://good.example": _iso(1),
        "https://bad.example":  "not-a-date",
        "https://none.example": None,
    }
    pruned = _prune_seen_urls(urls, retention_days=5)
    assert pruned == {"https://good.example": urls["https://good.example"]}


def test_load_seen_urls_returns_empty_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "SEEN_URLS_PATH", str(tmp_path / "seen_urls.json"))
    assert load_seen_urls() == {}


def test_load_seen_urls_returns_empty_on_corrupt_json(monkeypatch, tmp_path):
    path = tmp_path / "seen_urls.json"
    path.write_text("not valid json{")
    monkeypatch.setattr(utils, "SEEN_URLS_PATH", str(path))
    assert load_seen_urls() == {}


def test_mark_urls_seen_creates_and_appends(monkeypatch, tmp_path):
    path = tmp_path / "seen_urls.json"
    monkeypatch.setattr(utils, "SEEN_URLS_PATH", str(path))
    monkeypatch.setattr(utils, "DATA_DIR", str(tmp_path))

    mark_urls_seen(["https://a.example", "https://b.example"], retention_days=7)
    saved = json.loads(path.read_text())
    assert set(saved.keys()) == {"https://a.example", "https://b.example"}

    mark_urls_seen(["https://b.example", "https://c.example"], retention_days=7)
    saved = json.loads(path.read_text())
    assert set(saved.keys()) == {"https://a.example", "https://b.example", "https://c.example"}


def test_mark_urls_seen_preserves_original_timestamp(monkeypatch, tmp_path):
    """Re-marking an already-seen URL should NOT bump its timestamp forward."""
    path = tmp_path / "seen_urls.json"
    monkeypatch.setattr(utils, "SEEN_URLS_PATH", str(path))
    monkeypatch.setattr(utils, "DATA_DIR", str(tmp_path))

    mark_urls_seen(["https://a.example"], retention_days=7)
    first = json.loads(path.read_text())["https://a.example"]

    mark_urls_seen(["https://a.example", "https://b.example"], retention_days=7)
    second = json.loads(path.read_text())["https://a.example"]
    assert first == second


def test_mark_urls_seen_prunes_expired(monkeypatch, tmp_path):
    path = tmp_path / "seen_urls.json"
    monkeypatch.setattr(utils, "SEEN_URLS_PATH", str(path))
    monkeypatch.setattr(utils, "DATA_DIR", str(tmp_path))

    path.write_text(json.dumps({
        "https://stale.example": _iso(20),
        "https://fresh.example": _iso(1),
    }))
    mark_urls_seen(["https://new.example"], retention_days=5)

    saved = json.loads(path.read_text())
    assert "https://stale.example" not in saved
    assert "https://fresh.example" in saved
    assert "https://new.example" in saved
