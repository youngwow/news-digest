"""Tests for ImportanceBooster heuristics."""

from datetime import datetime, timedelta, timezone

from news_digest.config import HeuristicsConfig
from news_digest.digest.heuristics import ImportanceBooster
from news_digest.models import Story

FROZEN_NOW = datetime(2026, 5, 17, 14, 0, 0, tzinfo=timezone.utc)
HEUR = HeuristicsConfig(multi_source_max_boost=2, recency_window_hours=2,
                        recency_boost=1, source_weight_max_boost=1)


def _story(importance=5, sources=None, urls=None):
    return Story(title="x", category="политика", importance=importance,
                 sources=sources or ["Source A"], urls=urls or ["https://example.com/a"])


def _boost(story, url_published=None, source_weights=None):
    return ImportanceBooster(HEUR, url_published, source_weights).boost(story, now=FROZEN_NOW)


def test_single_source_recent_no_weight_keeps_importance():
    assert _boost(_story(5, ["A"], ["u1"])) == 5


def test_multi_source_boost_caps_at_max():
    assert _boost(_story(5, ["A", "B", "C", "D"])) == 7


def test_two_sources_gets_plus_one():
    assert _boost(_story(5, ["A", "B"])) == 6


def test_recency_boost_applies_when_any_url_is_recent():
    recent = (FROZEN_NOW - timedelta(hours=1)).isoformat()
    assert _boost(_story(5, urls=["u1"]), {"u1": recent}) == 6


def test_recency_boost_skips_old_articles():
    old = (FROZEN_NOW - timedelta(hours=6)).isoformat()
    assert _boost(_story(5, urls=["u1"]), {"u1": old}) == 5


def test_source_weight_boost_for_high_trust():
    assert _boost(_story(5, ["Meduza"]), source_weights={"Meduza": 2.0}) == 6


def test_source_weight_below_one_no_boost():
    assert _boost(_story(5, ["Noisy"]), source_weights={"Noisy": 0.5}) == 5


def test_importance_caps_at_ten():
    recent = (FROZEN_NOW - timedelta(hours=1)).isoformat()
    story = _story(9, ["A", "B", "C", "Meduza"], ["u1"])
    assert _boost(story, {"u1": recent}, {"Meduza": 2.0}) == 10


def test_malformed_timestamp_handled():
    assert _boost(_story(5, urls=["u1"]), {"u1": "not-a-date"}) == 5


def test_apply_returns_changed_count():
    stories = [_story(5, ["A", "B"]), _story(5, ["A"])]
    booster = ImportanceBooster(HEUR)
    assert booster.apply(stories, now=FROZEN_NOW) == 1
    assert stories[0].importance == 6
