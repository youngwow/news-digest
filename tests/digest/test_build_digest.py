"""Tests for DigestBuilder — the pure stories→Digest transform."""

from datetime import datetime

from news_digest.digest.builder import DigestBuilder
from news_digest.models import Story


def _story(title, importance, category="политика", urls=None, summary="", follow_up=None):
    return Story(title=title, importance=importance, category=category, sources=["src"],
                 urls=urls if urls is not None else [f"http://example.com/{importance}"],
                 short_summary=summary, follow_up_of=follow_up)


def _builder(config):
    return DigestBuilder(config.categories)


def test_headline_is_most_important(config):
    digest = _builder(config).build([_story("второстепенное", 3), _story("главное событие", 9)])
    assert digest.headline == "главное событие"


def test_top5_carries_primary_url(config):
    digest = _builder(config).build([_story("история", 8, urls=["http://a/1", "http://b/2"])])
    assert digest.top5[0].title == "история"
    assert digest.top5[0].url == "http://a/1"


def test_top5_without_urls_has_no_url(config):
    digest = _builder(config).build([_story("история", 8, urls=[])])
    assert digest.top5[0].url is None


def test_top5_carries_summary(config):
    digest = _builder(config).build([_story("история", 8, summary="Одно предложение.")])
    assert digest.top5[0].summary == "Одно предложение."


def test_top5_without_summary_is_none(config):
    digest = _builder(config).build([_story("история", 8)])
    assert digest.top5[0].summary is None


def test_thread_flag_propagates(config):
    threaded = _story("история с продолжением", 9, follow_up="вчера")
    plain = [_story(f"обычная {i}", 8 - i) for i in range(6)]
    digest = _builder(config).build([threaded] + plain)
    assert digest.top5[0].thread is True
    assert all(not e.thread for e in digest.top5[1:])


def test_rubrics_exclude_top5(config):
    stories = [_story(f"политика {i}", 10 - i) for i in range(7)]
    digest = _builder(config).build(stories)
    top5_titles = {e.title for e in digest.top5}
    rubric_titles = {e.title for e in digest.rubrics.get("политика", [])}
    assert not top5_titles & rubric_titles


def test_rest_has_no_duplicates(config):
    stories = [_story(f"история {i}", 5, category="мир") for i in range(12)]
    digest = _builder(config).build(stories)
    shown = {e.title for e in digest.top5}
    for items in digest.rubrics.values():
        shown |= {e.title for e in items}
    assert not shown & set(digest.rest)


def test_date_from_classified_at_local(config):
    classified_at = "2026-06-01T12:00:00Z"
    expected = datetime.fromisoformat("2026-06-01T12:00:00+00:00").astimezone().strftime("%d.%m.%Y")
    assert _builder(config).build([_story("x", 5)], classified_at).date == expected


def test_bad_classified_at_falls_back(config):
    assert _builder(config).build([_story("x", 5)], "not a date").date
