"""Tests for TelegramRenderer and split_message."""

from news_digest.digest.render import TelegramRenderer, _split_oversized_block, split_message
from news_digest.models import Digest, DigestDocument, Top5Entry

# ── split_message ───────────────────────────────────────────────────

def test_short_text_single_part():
    assert split_message("just a little text", limit=4096) == ["just a little text"]


def test_blocks_packed_under_limit():
    assert split_message("AAA\n\nBBB", limit=100) == ["AAA\n\nBBB"]


def test_split_on_block_boundary():
    a, b = "A" * 60, "B" * 60
    parts = split_message(f"{a}\n\n{b}", limit=100)
    assert parts == [a, b]


def test_three_blocks_pack_two_then_one():
    a, b, c = "A" * 40, "B" * 40, "C" * 40
    parts = split_message("\n\n".join([a, b, c]), limit=100)
    assert parts == [f"{a}\n\n{b}", c]


def test_oversized_block_falls_back_to_line_split():
    huge = "\n".join("line %02d" % i for i in range(20))
    parts = split_message(huge, limit=40)
    assert len(parts) >= 3
    for p in parts:
        assert all(len(line) <= 40 for line in p.split("\n"))


def test_split_oversized_helper_respects_limit():
    block = "\n".join(["A" * 20] * 5)
    for p in _split_oversized_block(block, limit=50):
        assert len(p) <= 50


# ── TelegramRenderer ────────────────────────────────────────────────

def _doc(top5, total_unique=2, total_raw=10) -> DigestDocument:
    return DigestDocument(
        digest=Digest(headline="Заголовок", date="01.06.2026", top5=top5, rubrics={}, rest=[]),
        total_unique=total_unique, total_raw=total_raw)


def _render(config, top5, **kw):
    return TelegramRenderer(config.categories).render(_doc(top5, **kw))


def test_top5_with_url_renders_markdown_link(config):
    text = _render(config, [Top5Entry(title="История", url="https://example.com/a")])
    assert "1. [История](https://example.com/a)" in text


def test_top5_without_url_title_only(config):
    text = _render(config, [Top5Entry(title="История")])
    assert "1. История" in text
    assert "http" not in text


def test_top5_link_then_summary(config):
    text = _render(config, [Top5Entry(title="История", summary="Краткая суть.",
                                      url="https://example.com/a")])
    assert "1. [История](https://example.com/a)\n   Краткая суть." in text


def test_thread_marker_with_link(config):
    text = _render(config, [Top5Entry(title="История", thread=True,
                                      url="https://example.com/a")])
    assert "1. 🔄 [История](https://example.com/a)" in text


def test_markdown_specials_escaped(config):
    text = _render(config, [Top5Entry(title="Скидка *50%* на _всё_")])
    assert r"Скидка \*50%\* на \_всё\_" in text


def test_footer_pluralization(config):
    text = _render(config, [Top5Entry(title="x")], total_unique=51, total_raw=53)
    assert "51 сюжет / 53 статьи" in text
