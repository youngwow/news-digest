"""Tests for fetch_bodies: HTML extraction, cache roundtrip, error handling."""

import fetch_bodies
from fetch_bodies import _cache_path, _read_cache, _write_cache, extract_paragraphs

# ── HTML extraction ────────────────────────────────────────────────

def test_extract_paragraphs_prefers_article_tag():
    html = """
    <html><body>
        <p>Sidebar noise</p>
        <article>
            <p>Main first paragraph.</p>
            <p>Main second paragraph.</p>
        </article>
    </body></html>
    """
    text = extract_paragraphs(html)
    assert "Main first paragraph." in text
    assert "Main second paragraph." in text
    assert "Sidebar noise" not in text


def test_extract_paragraphs_falls_back_to_main():
    html = """
    <html><body>
        <main>
            <p>Story text here.</p>
        </main>
    </body></html>
    """
    assert "Story text here." in extract_paragraphs(html)


def test_extract_paragraphs_falls_back_to_body():
    html = "<html><body><p>Bare body paragraph.</p></body></html>"
    assert extract_paragraphs(html).strip() == "Bare body paragraph."


def test_extract_paragraphs_skips_script_and_style():
    html = """
    <html><body>
        <script>var x = "noise";</script>
        <style>.foo { color: red }</style>
        <p>Real content.</p>
    </body></html>
    """
    text = extract_paragraphs(html)
    assert "Real content." in text
    assert "noise" not in text
    assert "color: red" not in text


def test_extract_paragraphs_handles_html_entities():
    html = "<body><p>Russia &amp; Ukraine</p></body>"
    assert "Russia & Ukraine" in extract_paragraphs(html)


def test_extract_paragraphs_empty_when_no_p_tags():
    assert extract_paragraphs("<body><div>no paragraphs</div></body>") == ""


def test_extract_paragraphs_handles_malformed_html():
    # Should not raise; may return empty
    result = extract_paragraphs("<html><body><p>unterminated")
    assert isinstance(result, str)


# ── cache roundtrip ────────────────────────────────────────────────

def test_cache_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_bodies, "CACHE_DIR", str(tmp_path))
    url = "https://example.com/article-1"
    assert _read_cache(url) is None
    _write_cache(url, "cached content here")
    assert _read_cache(url) == "cached content here"


def test_cache_path_is_content_addressed(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_bodies, "CACHE_DIR", str(tmp_path))
    p1 = _cache_path("https://a.example/x")
    p2 = _cache_path("https://a.example/x")
    p3 = _cache_path("https://b.example/x")
    assert p1 == p2 and p1 != p3
    assert p1.endswith(".txt")
