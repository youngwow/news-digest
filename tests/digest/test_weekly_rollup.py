"""Tests for WeeklyRollup and DigestArchive.recent_stories."""

import json
from datetime import datetime, timezone

from digest.support import valid_raw_config
from news_digest.config import Config
from news_digest.digest.archive import DigestArchive
from news_digest.digest.rollup import WeeklyRollup
from news_digest.models import Story
from news_digest.paths import ProjectPaths

NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


def _story(title, importance=5):
    return Story(title=title, importance=importance, category="политика",
                 sources=["src"], urls=[f"http://example.com/{title}"])


def _write_snapshot(dirpath, ts, stories):
    (dirpath / f"digest-{ts}.json").write_text(
        json.dumps({"all_stories": [s.to_dict() for s in stories]}, ensure_ascii=False),
        encoding="utf-8")


def test_timestamp_parsing():
    assert DigestArchive("x").timestamp_of("digest-2026-05-17T18-16.json") == \
        datetime(2026, 5, 17, 18, 16, tzinfo=timezone.utc)
    assert DigestArchive("x").timestamp_of("digest-garbage.json") is None


def test_recent_stories_window(tmp_path):
    _write_snapshot(tmp_path, "2026-06-09T10-00", [_story("свежая")])
    _write_snapshot(tmp_path, "2026-05-20T10-00", [_story("старая")])
    stories = DigestArchive(str(tmp_path)).recent_stories(lookback_days=7, now=NOW)
    assert [s.title for s in stories] == ["свежая"]


def test_recent_stories_tolerates_corrupt(tmp_path):
    (tmp_path / "digest-2026-06-09T10-00.json").write_text("{broken", encoding="utf-8")
    _write_snapshot(tmp_path, "2026-06-10T09-00", [_story("живая")])
    stories = DigestArchive(str(tmp_path)).recent_stories(lookback_days=7, now=NOW)
    assert [s.title for s in stories] == ["живая"]


def _rollup(tmp_path):
    cfg = Config.from_dict(valid_raw_config())
    paths = ProjectPaths.from_root(str(tmp_path))
    archive = DigestArchive(str(tmp_path))
    return WeeklyRollup(cfg, paths, archive=archive)


def test_rollup_merges_and_dedups_across_days(tmp_path):
    _write_snapshot(tmp_path, "2026-06-08T10-00",
                    [_story("Россия атаковала Украину массированным ударом дронов", 8)])
    _write_snapshot(tmp_path, "2026-06-09T10-00",
                    [_story("Россия атаковала Украину массированным ударом ракет и дронов", 7),
                     _story("Запуск новой ракеты-носителя с космодрома Восточный", 5)])
    doc = _rollup(tmp_path).build(days=7, now=NOW)
    assert doc.total_raw == 3
    assert doc.total_unique == 2
    assert doc.digest.headline


def test_rollup_empty_returns_none(tmp_path):
    assert _rollup(tmp_path).build(days=7, now=NOW) is None
