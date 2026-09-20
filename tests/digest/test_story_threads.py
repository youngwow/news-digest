"""Tests for StoryThreader — follow-up annotation with an injected fake encoder."""

import json

from digest.support import FakeEncoder
from news_digest.config import ThreadsConfig
from news_digest.digest.archive import DigestArchive
from news_digest.digest.threads import StoryThreader
from news_digest.models import Story


def _story(title):
    return Story(title=title, category="политика", importance=5,
                 sources=["src"], urls=["http://a.com/1"])


def _threader(encoder=None, enabled=True, threshold=0.6):
    return StoryThreader(ThreadsConfig(enabled=enabled, lookback_days=3,
                                       similarity_threshold=threshold), encoder)


def test_followup_above_threshold_annotated():
    stories = [_story("Дерипаска потребовал взыскать 1 млрд")]
    archive = [_story("Дерипаска подал иск к расследователям"), _story("другая старая новость")]
    enc = FakeEncoder({
        "Дерипаска потребовал взыскать 1 млрд": [1.0, 0.2],
        "Дерипаска подал иск к расследователям": [1.0, 0.3],
        "другая старая новость": [0.0, 1.0],
    })
    n = _threader(enc).annotate(stories, archive)
    assert n == 1
    assert stories[0].follow_up_of == "Дерипаска подал иск к расследователям"


def test_best_archive_match_wins():
    stories = [_story("новость")]
    archive = [_story("слабое совпадение"), _story("сильное совпадение")]
    enc = FakeEncoder({"новость": [1.0, 0.0], "слабое совпадение": [0.8, 0.6],
                       "сильное совпадение": [0.99, 0.14]})
    _threader(enc).annotate(stories, archive)
    assert stories[0].follow_up_of == "сильное совпадение"


def test_below_threshold_untouched():
    stories = [_story("свежая новость")]
    enc = FakeEncoder({"свежая новость": [1.0, 0.0], "несвязанная": [0.3, 0.95]})
    assert _threader(enc).annotate(stories, [_story("несвязанная")]) == 0
    assert stories[0].follow_up_of is None


def test_empty_archive_is_noop():
    assert _threader(FakeEncoder({})).annotate([_story("x")], []) == 0


def test_no_encoder_is_noop():
    assert _threader(encoder=None).annotate([_story("x")], [_story("y")]) == 0


def _write_snapshot(dirpath, ts, stories):
    (dirpath / f"digest-{ts}.json").write_text(
        json.dumps({"all_stories": [s.to_dict() for s in stories]}, ensure_ascii=False),
        encoding="utf-8")


def test_disabled_flag_is_noop(tmp_path):
    archive = DigestArchive(str(tmp_path))
    _write_snapshot(tmp_path, "2099-01-01T00-00", [_story("старая")])
    threader = _threader(FakeEncoder({}), enabled=False)
    assert threader.annotate_from_archive([_story("новая")], archive, None) == 0


def test_missing_archive_is_noop(tmp_path):
    archive = DigestArchive(str(tmp_path / "nope"))
    assert _threader(FakeEncoder({}), enabled=True).annotate_from_archive(
        [_story("новая")], archive, None) == 0
