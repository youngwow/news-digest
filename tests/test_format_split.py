"""Tests for block-aware Telegram message splitting in format_telegram."""

from format_telegram import _split_oversized_block, split_message


def _two_block_text(block_a: str, block_b: str) -> str:
    return f"{block_a}\n\n{block_b}"


def test_short_text_returns_single_part():
    parts = split_message("just a little text", limit=4096)
    assert parts == ["just a little text"]


def test_blocks_packed_together_when_under_limit():
    text = _two_block_text("AAA", "BBB")
    parts = split_message(text, limit=100)
    assert len(parts) == 1
    assert parts[0] == "AAA\n\nBBB"


def test_split_on_block_boundary_when_packed_blocks_exceed_limit():
    # Two ~60-char blocks, limit 100 → must split between them (not within)
    a = "A" * 60
    b = "B" * 60
    parts = split_message(_two_block_text(a, b), limit=100)
    assert len(parts) == 2
    assert parts[0] == a
    assert parts[1] == b


def test_three_blocks_pack_two_then_one():
    a = "A" * 40
    b = "B" * 40
    c = "C" * 40
    text = "\n\n".join([a, b, c])
    # limit 100 → "A\n\nB" = 82 chars fits; "...\n\nC" would be 124 → flush, then C
    parts = split_message(text, limit=100)
    assert len(parts) == 2
    assert parts[0] == f"{a}\n\n{b}"
    assert parts[1] == c


def test_oversized_block_falls_back_to_line_split():
    # A single block that exceeds the limit must still be split — line-by-line
    huge_block = "\n".join(["line %02d" % i for i in range(20)])  # 20 lines, ~140 chars
    parts = split_message(huge_block, limit=40)
    assert len(parts) >= 3
    # Each part still ends on a line boundary
    for p in parts:
        assert all(len(line) <= 40 for line in p.split("\n"))


def test_split_oversized_block_helper_respects_limit():
    block = "\n".join(["A" * 20] * 5)  # 5 lines, ~105 chars total
    parts = _split_oversized_block(block, limit=50)
    for p in parts:
        assert len(p) <= 50


def test_digest_shape_keeps_categories_together():
    """Simulate a real digest layout — each category block should land in a single part."""
    blocks = [
        "📰 Header",
        "🔥 Headline",
        "▸▸▸ Главное ▸▸▸\n1. story\n2. story",
        "▸▸▸ По темам ▸▸▸\n🔹 Политика\n→ a\n→ b\n→ c",
        "🔹 Мир\n→ x\n→ y",
        "▸▸▸ Также ▸▸▸\n• z1\n• z2",
        "— footer",
    ]
    text = "\n\n".join(blocks)
    # Tight limit forces multiple parts but each block stays intact
    parts = split_message(text, limit=80)
    rejoined = "\n\n".join(parts)
    # No category text is broken — every block string appears intact in some part
    for block in blocks:
        if len(block) <= 80:
            assert any(block in p for p in parts), f"block split: {block!r}"
    # Round-trip preserves content (modulo block separator collapse)
    assert rejoined.replace("\n\n", "") == text.replace("\n\n", "")
