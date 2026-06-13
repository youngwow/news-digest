"""Tests for news_digest.sources.bodies — HTML extraction + cache roundtrip."""

from news_digest.config import ScraperConfig
from news_digest.sources.bodies import BodyEnricher, extract_paragraphs


def test_extract_paragraphs_prefers_article_tag():
    html = """<html><body>
        <p>Sidebar noise</p>
        <article><p>Main first paragraph.</p><p>Main second paragraph.</p></article>
    </body></html>"""
    text = extract_paragraphs(html)
    assert "Main first paragraph." in text
    assert "Main second paragraph." in text
    assert "Sidebar noise" not in text


def test_extract_paragraphs_falls_back_to_main():
    assert "Story text here." in extract_paragraphs(
        "<html><body><main><p>Story text here.</p></main></body></html>")


def test_extract_paragraphs_falls_back_to_body():
    assert extract_paragraphs("<html><body><p>Bare body paragraph.</p></body></html>").strip() \
        == "Bare body paragraph."


def test_extract_paragraphs_skips_script_and_style():
    html = """<html><body><script>var x = "noise";</script>
        <style>.foo { color: red }</style><p>Real content.</p></body></html>"""
    text = extract_paragraphs(html)
    assert "Real content." in text
    assert "noise" not in text
    assert "color: red" not in text


def test_extract_paragraphs_handles_html_entities():
    assert "Russia & Ukraine" in extract_paragraphs("<body><p>Russia &amp; Ukraine</p></body>")


def test_extract_paragraphs_empty_when_no_p_tags():
    assert extract_paragraphs("<body><div>no paragraphs</div></body>") == ""


def test_extract_paragraphs_handles_malformed_html():
    assert isinstance(extract_paragraphs("<html><body><p>unterminated"), str)


def _enricher(tmp_path) -> BodyEnricher:
    cfg = ScraperConfig(date_window_hours=8, request_timeout=20, max_redirects=5,
                        user_agent="x", fetch_bodies=True, body_max_chars=3000,
                        body_timeout=10, body_concurrency=8, body_cache_retention_days=14)
    return BodyEnricher(cfg, str(tmp_path))


def test_cache_roundtrip(tmp_path):
    e = _enricher(tmp_path)
    url = "https://example.com/article-1"
    assert e._read_cache(url) is None
    e._write_cache(url, "cached content here")
    assert e._read_cache(url) == "cached content here"


def test_cache_path_is_content_addressed(tmp_path):
    e = _enricher(tmp_path)
    p1 = e._cache_path("https://a.example/x")
    p2 = e._cache_path("https://a.example/x")
    p3 = e._cache_path("https://b.example/x")
    assert p1 == p2 and p1 != p3
    assert p1.endswith(".txt")
