#!/usr/bin/env python3
"""
Convert digest.json to compact Telegram-friendly markdown.
Output to stdout — designed for delivery as message body (no file).
Format: emoji separators, titles only (no summaries).
"""

import json
import os

from utils import CONFIG, DATA_DIR, get_logger, pluralize_ru

DIGEST_JSON    = os.path.join(DATA_DIR, "digest.json")
TELEGRAM_LIMIT = CONFIG["telegram"]["message_limit"]
CAT_LABEL      = CONFIG["categories"]["labels"]

log = get_logger("format_telegram")


def load_digest(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _split_oversized_block(block: str, limit: int) -> list[str]:
    """Fall-back line-level split for a single block that exceeds the limit."""
    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in block.split("\n"):
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            parts.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        parts.append("\n".join(current))
    return parts


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split text into Telegram-sized parts on block boundaries (\\n\\n) where possible.

    `format_digest` separates sections with blank lines, so packing blocks keeps
    a category header together with its items instead of breaking mid-section.
    Single blocks that exceed the limit fall back to line-level splitting.
    """
    blocks = text.split("\n\n")
    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            parts.append("\n\n".join(current))
            current = []
            current_len = 0

    for block in blocks:
        block_len = len(block) + (2 if current else 0)  # +2 for "\n\n" separator
        if block_len > limit:
            # Oversized block — flush what we have, then split this block line-by-line
            flush()
            parts.extend(_split_oversized_block(block, limit))
            continue
        if current and current_len + block_len > limit:
            flush()
            block_len = len(block)
        current.append(block)
        current_len += block_len
    flush()

    return parts


def format_digest(digest_data: dict) -> str:
    digest = digest_data.get("digest", digest_data)
    total = digest_data.get("total_unique", "?")
    total_raw = digest_data.get("total_raw", "?")

    lines = []

    # Header
    date_str = digest.get("date", "сегодня")
    lines.append(f"📰 Дайджест новостей — {date_str}")
    lines.append("")

    # Headline
    headline = digest.get("headline", "")
    if headline:
        lines.append(f"🔥 {headline}")
        lines.append("")

    # Top-5 — title, one-sentence summary, primary link (plain text; Telegram auto-links)
    top5 = digest.get("top5", [])
    if top5:
        lines.append("▸▸▸ Главное ▸▸▸")
        for i, item in enumerate(top5[:5], 1):
            mark = "🔄 " if item.get("thread") else ""
            lines.append(f"{i}. {mark}{item.get('title', '')}")
            if item.get("summary"):
                lines.append(f"   {item['summary']}")
            if item.get("url"):
                lines.append(f"   {item['url']}")
        lines.append("")

    # Rubrics — titles only
    rubrics = digest.get("rubrics", {})
    if rubrics:
        lines.append("▸▸▸ По темам ▸▸▸")
        for cat_name, items in rubrics.items():
            label = CAT_LABEL.get(cat_name, f"📌 {cat_name}")
            lines.append(f"🔹 {label}")
            for item in items[:5]:
                mark = "🔄 " if item.get("thread") else ""
                lines.append(f"→ {mark}{item.get('title', '')}")
            lines.append("")

    # Rest — titles only
    rest = digest.get("rest", [])
    if rest:
        lines.append("▸▸▸ Также в новостях ▸▸▸")
        for title in rest[:25]:
            lines.append(f"• {title}")
        lines.append("")

    # Footer
    stories_word = pluralize_ru(total, "сюжет", "сюжета", "сюжетов") if isinstance(total, int) else "сюжетов"
    articles_word = pluralize_ru(total_raw, "статья", "статьи", "статей") if isinstance(total_raw, int) else "статей"
    lines.append(f"— {total} {stories_word} / {total_raw} {articles_word} / {date_str}")

    return "\n".join(lines)


def main() -> None:
    if not os.path.exists(DIGEST_JSON):
        log.error("digest.json not found at %s", DIGEST_JSON)
        raise SystemExit(1)

    data = load_digest(DIGEST_JSON)
    output = format_digest(data)
    parts = split_message(output)

    if len(parts) > 1:
        log.info("digest split into %d parts — Telegram limit %d chars", len(parts), TELEGRAM_LIMIT)

    for i, part in enumerate(parts, 1):
        if len(parts) > 1:
            print(f"\n[{i}/{len(parts)}]")
        print(part)


if __name__ == "__main__":
    main()
