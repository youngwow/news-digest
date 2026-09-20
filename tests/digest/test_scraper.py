"""Tests for news_digest.sources.scraper helpers — URL cleaning, boilerplate, source selection."""

from news_digest.models import Article
from news_digest.sources.scraper import Scraper, clean_url, is_boilerplate, strip_html

# ── clean_url ───────────────────────────────────────────────────────

def test_strips_at_campaign_params():
    url = "https://www.bbc.com/russian/articles/cy49nr72wmvo?at_medium=RSS&at_campaign=rss"
    assert clean_url(url) == "https://www.bbc.com/russian/articles/cy49nr72wmvo"


def test_strips_utm_and_maca():
    url = "https://www.dw.com/ru/x/a-77490948?maca=rus-rss-ru-all-1126-rdf&utm_source=feed"
    assert clean_url(url) == "https://www.dw.com/ru/x/a-77490948"


def test_strips_click_ids():
    assert clean_url("https://a.com/p?fbclid=abc&gclid=def&yclid=ghi&ref=tw") == "https://a.com/p"


def test_preserves_meaningful_params():
    assert clean_url("https://example.com/article?id=123&page=2&utm_campaign=x") \
        == "https://example.com/article?id=123&page=2"


def test_no_query_unchanged():
    assert clean_url("https://meduza.io/news/2026/06/10/story") \
        == "https://meduza.io/news/2026/06/10/story"


def test_preserves_fragment():
    assert clean_url("https://a.com/p?utm_source=x#section") == "https://a.com/p#section"


def test_empty_url():
    assert clean_url("") == ""


# ── strip_html / boilerplate ────────────────────────────────────────

def test_strip_html_unescapes_entities():
    assert strip_html("<b>Russia</b> &amp; Ukraine") == "Russia & Ukraine"


def test_is_boilerplate_live_and_podcast():
    assert is_boilerplate(Article(url="https://bbc.com/russian/live/abc"))
    assert is_boilerplate(Article(url="https://bbc.com/russian/podcasts/x"))


def test_is_boilerplate_generic_undated():
    assert is_boilerplate(Article(url="https://x.com/a", published=None,
                                  summary="Latest news and updates from us"))


def test_real_article_is_not_boilerplate():
    assert not is_boilerplate(Article(url="https://meduza.io/news/2026/06/10/story",
                                      published="2026-06-10T10:00:00+00:00", summary="real"))


# ── feed_url: which sources.json entries the digest polls ───────────

def test_feed_url_uses_fetch_url_of_rss_entries():
    src = {"name": "CNews", "url": "https://www.cnews.ru", "kind": "rss",
           "fetch_url": "https://www.cnews.ru/inc/rss/biz.xml"}
    assert Scraper.feed_url(src) == "https://www.cnews.ru/inc/rss/biz.xml"


def test_feed_url_skips_disabled_entries():
    src = {"name": "РБК", "kind": "rss", "fetch_url": "https://rssexport.rbc.ru/x.rss",
           "enabled": False}
    assert Scraper.feed_url(src) is None


def test_feed_url_skips_non_rss_kinds():
    for kind in ("telegram", "html", "sitemap", "search"):
        assert Scraper.feed_url({"name": "x", "kind": kind, "fetch_url": "https://x"}) is None


def test_feed_url_honours_legacy_rss_key():
    assert Scraper.feed_url({"name": "Meduza", "rss": "https://meduza.io/rss/all"}) \
        == "https://meduza.io/rss/all"


def test_feed_url_without_any_url_is_none():
    assert Scraper.feed_url({"name": "nothing", "kind": "rss"}) is None
