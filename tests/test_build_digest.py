"""Tests for analyze_full.build_digest — the pure stories→digest transform."""

from analyze_full import build_digest


def _story(title: str, importance: int, category: str = "политика",
           urls: list[str] | None = None) -> dict:
    return {
        "title": title,
        "importance": importance,
        "category": category,
        "sources": ["src"],
        "urls": urls if urls is not None else [f"http://example.com/{importance}"],
    }


def test_headline_is_most_important_story():
    stories = [_story("второстепенное", 3), _story("главное событие дня", 9)]
    digest = build_digest(stories)
    assert digest["headline"] == "главное событие дня"


def test_top5_carries_primary_url():
    stories = [_story("история", 8, urls=["http://a.com/1", "http://b.com/2"])]
    digest = build_digest(stories)
    assert digest["top5"][0] == {"title": "история", "url": "http://a.com/1"}


def test_top5_without_urls_has_no_url_key():
    stories = [_story("история", 8, urls=[])]
    digest = build_digest(stories)
    assert digest["top5"][0] == {"title": "история"}


def test_top5_carries_short_summary():
    story = _story("история", 8)
    story["short_summary"] = "Одно предложение о событии."
    digest = build_digest([story])
    assert digest["top5"][0]["summary"] == "Одно предложение о событии."


def test_top5_without_summary_has_no_summary_key():
    digest = build_digest([_story("история", 8)])
    assert "summary" not in digest["top5"][0]


def test_thread_flag_propagates_to_top5_and_rubrics():
    threaded = _story("история с продолжением", 9)
    threaded["follow_up_of"] = "вчерашняя история"
    plain_stories = [_story(f"обычная история {i}", 8 - i) for i in range(6)]
    digest = build_digest([threaded] + plain_stories)
    assert digest["top5"][0]["thread"] is True
    assert all("thread" not in e for e in digest["top5"][1:])
    rubric_entries = digest["rubrics"]["политика"]
    assert all("thread" not in e for e in rubric_entries)  # plain stories unmarked


def test_rubrics_exclude_top5_titles():
    stories = [_story(f"политика номер {i}", 10 - i) for i in range(7)]
    digest = build_digest(stories)
    top5_titles = {e["title"] for e in digest["top5"]}
    rubric_titles = {e["title"] for e in digest["rubrics"]["политика"]}
    assert not top5_titles & rubric_titles


def test_rest_has_no_duplicates_with_rubrics():
    stories = [_story(f"история {i}", 5, category="спорт") for i in range(12)]
    digest = build_digest(stories)
    shown = {e["title"] for e in digest["top5"]}
    for items in digest["rubrics"].values():
        shown |= {e["title"] for e in items}
    assert not shown & set(digest["rest"])


def test_date_from_classified_at_in_local_time():
    # Expected value computed with the same local-time conversion → TZ-independent
    from datetime import datetime
    classified_at = "2026-06-01T12:00:00Z"
    expected = datetime.fromisoformat("2026-06-01T12:00:00+00:00").astimezone().strftime("%d.%m.%Y")
    digest = build_digest([_story("x", 5)], classified_at=classified_at)
    assert digest["date"] == expected


def test_bad_classified_at_falls_back_to_today():
    digest = build_digest([_story("x", 5)], classified_at="not a date")
    assert digest["date"]  # falls back to now — just must not raise
