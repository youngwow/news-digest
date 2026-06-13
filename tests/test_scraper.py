"""Tests for scraper.py helpers — URL cleaning."""

from scraper import clean_url


def test_strips_at_campaign_params():
    url = "https://www.bbc.com/russian/articles/cy49nr72wmvo?at_medium=RSS&at_campaign=rss"
    assert clean_url(url) == "https://www.bbc.com/russian/articles/cy49nr72wmvo"


def test_strips_utm_and_maca():
    url = "https://www.dw.com/ru/x/a-77490948?maca=rus-rss-ru-all-1126-rdf&utm_source=feed"
    assert clean_url(url) == "https://www.dw.com/ru/x/a-77490948"


def test_strips_click_ids():
    assert clean_url("https://a.com/p?fbclid=abc&gclid=def&yclid=ghi&ref=tw") == "https://a.com/p"


def test_preserves_meaningful_params():
    url = "https://example.com/article?id=123&page=2&utm_campaign=x"
    assert clean_url(url) == "https://example.com/article?id=123&page=2"


def test_no_query_unchanged():
    assert clean_url("https://meduza.io/news/2026/06/10/story") == "https://meduza.io/news/2026/06/10/story"


def test_preserves_fragment():
    assert clean_url("https://a.com/p?utm_source=x#section") == "https://a.com/p#section"


def test_empty_url():
    assert clean_url("") == ""
