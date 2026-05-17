"""Tests for boost_importance() heuristics in merge_chunks.py."""

from datetime import datetime, timedelta, timezone

from merge_chunks import boost_importance


def _story(importance=5, sources=None, urls=None):
    return {
        "title": "x",
        "category": "политика",
        "importance": importance,
        "sources": sources or ["Source A"],
        "urls": urls or ["https://example.com/a"],
    }


FROZEN_NOW = datetime(2026, 5, 17, 14, 0, 0, tzinfo=timezone.utc)


def test_single_source_recent_no_weight_keeps_importance():
    story = _story(importance=5, sources=["A"], urls=["u1"])
    assert boost_importance(story, {}, {}, now=FROZEN_NOW) == 5


def test_multi_source_boost_caps_at_max():
    # 4 unique sources → +min(3, 2) = +2 boost
    story = _story(importance=5, sources=["A", "B", "C", "D"])
    assert boost_importance(story, {}, {}, now=FROZEN_NOW) == 7


def test_two_sources_gets_plus_one():
    story = _story(importance=5, sources=["A", "B"])
    assert boost_importance(story, {}, {}, now=FROZEN_NOW) == 6


def test_recency_boost_applies_when_any_url_is_recent():
    one_hour_ago = (FROZEN_NOW - timedelta(hours=1)).isoformat()
    url_published = {"u1": one_hour_ago}
    story = _story(importance=5, urls=["u1"])
    assert boost_importance(story, url_published, {}, now=FROZEN_NOW) == 6


def test_recency_boost_does_not_apply_for_old_articles():
    six_hours_ago = (FROZEN_NOW - timedelta(hours=6)).isoformat()
    url_published = {"u1": six_hours_ago}
    story = _story(importance=5, urls=["u1"])
    assert boost_importance(story, url_published, {}, now=FROZEN_NOW) == 5


def test_source_weight_boost_applies_for_high_trust_source():
    story = _story(importance=5, sources=["Meduza"])
    assert boost_importance(story, {}, {"Meduza": 2.0}, now=FROZEN_NOW) == 6


def test_source_weight_below_one_gives_no_boost():
    story = _story(importance=5, sources=["Noisy"])
    assert boost_importance(story, {}, {"Noisy": 0.5}, now=FROZEN_NOW) == 5


def test_importance_caps_at_ten():
    one_hour_ago = (FROZEN_NOW - timedelta(hours=1)).isoformat()
    story = _story(importance=9, sources=["A", "B", "C", "Meduza"], urls=["u1"])
    # 9 + 2 (multi) + 1 (weight) + 1 (recent) = 13 → cap at 10
    boosted = boost_importance(
        story, {"u1": one_hour_ago}, {"Meduza": 2.0}, now=FROZEN_NOW,
    )
    assert boosted == 10


def test_handles_malformed_published_timestamp_gracefully():
    story = _story(importance=5, urls=["u1"])
    # Bad timestamp shouldn't blow up; just no recency boost
    assert boost_importance(story, {"u1": "not-a-date"}, {}, now=FROZEN_NOW) == 5
