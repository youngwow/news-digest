#!/usr/bin/env python3
"""
Convert digest.json to compact Telegram-friendly markdown.
Output to stdout — designed for delivery as message body (no file).
Format: emoji separators, titles only (no summaries).
"""

import json
import os
import sys

from utils import CONFIG, DATA_DIR

DIGEST_JSON    = os.path.join(DATA_DIR, "digest.json")
TELEGRAM_LIMIT = CONFIG["telegram"]["message_limit"]
CAT_LABEL      = CONFIG["categories"]["labels"]


def load_digest(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split text into parts that fit within Telegram's character limit, breaking on line boundaries."""
    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        line_len = len(line) + 1  # +1 for the newline join will add
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

    # Top-5 — titles only
    top5 = digest.get("top5", [])
    if top5:
        lines.append("▸▸▸ Главное ▸▸▸")
        for i, item in enumerate(top5[:5], 1):
            lines.append(f"{i}. {item.get('title', '')}")
        lines.append("")

    # Rubrics — titles only
    rubrics = digest.get("rubrics", {})
    if rubrics:
        lines.append("▸▸▸ По темам ▸▸▸")
        for cat_name, items in rubrics.items():
            label = CAT_LABEL.get(cat_name, f"📌 {cat_name}")
            lines.append(f"🔹 {label}")
            for item in items[:5]:
                lines.append(f"→ {item.get('title', '')}")
            lines.append("")

    # Rest — titles only
    rest = digest.get("rest", [])
    if rest:
        lines.append("▸▸▸ Также в новостях ▸▸▸")
        for title in rest[:25]:
            lines.append(f"• {title}")
        lines.append("")

    # Footer
    lines.append(f"— {total} сюжетов / {total_raw} статей / {date_str}")

    return "\n".join(lines)


def main() -> None:
    if not os.path.exists(DIGEST_JSON):
        print("ERROR: digest.json not found", file=sys.stderr)
        raise SystemExit(1)

    data = load_digest(DIGEST_JSON)
    output = format_digest(data)
    parts = split_message(output)

    if len(parts) > 1:
        print(f"[digest split into {len(parts)} parts — Telegram limit {TELEGRAM_LIMIT} chars]",
              file=sys.stderr)

    for i, part in enumerate(parts, 1):
        if len(parts) > 1:
            print(f"\n[{i}/{len(parts)}]")
        print(part)


if __name__ == "__main__":
    main()
