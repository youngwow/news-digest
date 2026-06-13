"""Tests for weekly_rollup.py — snapshot collection and rollup building."""

import json
from datetime import datetime, timezone

from weekly_rollup import build_rollup, collect_stories, snapshot_timestamp

NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


def _story(title: str, importance: int = 5) -> dict:
    return {"title": title, "importance": importance, "category": "политика",
            "sources": ["src"], "urls": [f"http://example.com/{title}"]}


def _write_snapshot(dirpath, ts: str, stories: list[dict]) -> None:
    path = dirpath / f"digest-{ts}.json"
    path.write_text(json.dumps({"all_stories": stories}, ensure_ascii=False),
                    encoding="utf-8")


# ── snapshot_timestamp ──────────────────────────────────────────────

def test_snapshot_timestamp_parses_archive_filename():
    ts = snapshot_timestamp("digest-2026-05-17T18-16.json")
    assert ts == datetime(2026, 5, 17, 18, 16, tzinfo=timezone.utc)


def test_snapshot_timestamp_rejects_garbage():
    assert snapshot_timestamp("digest-garbage.json") is None


# ── collect_stories ─────────────────────────────────────────────────

def test_collect_skips_snapshots_outside_window(tmp_path):
    _write_snapshot(tmp_path, "2026-06-09T10-00", [_story("свежая история")])
    _write_snapshot(tmp_path, "2026-05-20T10-00", [_story("старая история")])
    stories = collect_stories(str(tmp_path), days=7, now=NOW)
    assert [s["title"] for s in stories] == ["свежая история"]


def test_collect_missing_dir_returns_empty():
    assert collect_stories("/nonexistent/dir", days=7, now=NOW) == []


def test_collect_tolerates_unreadable_snapshot(tmp_path):
    (tmp_path / "digest-2026-06-09T10-00.json").write_text("{broken", encoding="utf-8")
    _write_snapshot(tmp_path, "2026-06-10T09-00", [_story("живая история")])
    stories = collect_stories(str(tmp_path), days=7, now=NOW)
    assert [s["title"] for s in stories] == ["живая история"]


# ── build_rollup ────────────────────────────────────────────────────

def test_build_rollup_merges_and_dedups_across_days(tmp_path):
    # The same event phrased twice across two days must collapse into one story
    _write_snapshot(tmp_path, "2026-06-08T10-00",
                    [_story("Россия атаковала Украину массированным ударом дронов", 8)])
    _write_snapshot(tmp_path, "2026-06-09T10-00",
                    [_story("Россия атаковала Украину массированным ударом ракет и дронов", 7),
                     _story("Запуск новой ракеты-носителя с космодрома Восточный", 5)])
    full = build_rollup(str(tmp_path), days=7, now=NOW)
    assert full["total_raw"] == 3
    assert full["total_unique"] == 2
    assert full["rollup_days"] == 7
    assert full["digest"]["headline"]


def test_build_rollup_returns_none_when_empty(tmp_path):
    assert build_rollup(str(tmp_path), days=7, now=NOW) is None
