#!/usr/bin/env python3
"""
Convert digest.json to compact Telegram-friendly markdown.
Output to stdout — designed for delivery as message body (no file).
Format: emoji separators, no tables, compact, no markdown stars.
"""

import json
import os


HERE = os.path.dirname(os.path.abspath(__file__))
DIGEST_JSON = os.path.join(HERE, "digest.json")

# Category labels with emoji
CAT_LABEL = {
    "политика": "🏛️ Политика",
    "экономика": "💰 Экономика",
    "технологии": "🤖 Технологии",
    "мир": "🌍 Мир",
    "спорт": "⚽ Спорт",
    "наука": "🔬 Наука",
    "культура": "🎭 Культура",
    "прочее": "📌 Прочее",
}

MAX_ITEM_LEN = 300  # truncate long bodies


def truncate(text: str, max_len: int = MAX_ITEM_LEN) -> str:
    """Truncate text to max_len chars, breaking at word boundary."""
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


def load_digest(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def format_digest(digest_data: dict) -> str:
    digest = digest_data.get("digest", digest_data)
    total = digest_data.get("total_unique", "?")
    total_raw = digest_data.get("total_raw", "?")

    lines = []

    # Header
    date_str = digest.get("date", "сегодня")
    lines.append(f"📰 Дайджест новостей — {date_str}")
    lines.append("")

    # Headline of the day
    headline = digest.get("headline", "")
    if headline:
        lines.append(f"🔥 {headline}")
        lines.append("")

    # Top-5
    top5 = digest.get("top5", [])
    if top5:
        lines.append("▸▸▸ Главное ▸▸▸")
        for i, item in enumerate(top5[:5], 1):
            title = item.get("title", "")
            body = truncate(item.get("body", ""))
            lines.append(f"{i}. {title}")
            if body and body != title:
                lines.append(f"   {body}")
            lines.append("")

    # Rubrics
    rubrics = digest.get("rubrics", {})
    if rubrics:
        lines.append("▸▸▸ По темам ▸▸▸")
        for cat_name, items in rubrics.items():
            label = CAT_LABEL.get(cat_name, f"📌 {cat_name}")
            lines.append(f"🔹 {label}")
            for item in items[:5]:  # top 5 per category
                title = item.get("title", "")
                body = truncate(item.get("body", ""))
                if body and body != title:
                    lines.append(f"→ {title} — {body}")
                else:
                    lines.append(f"→ {title}")
            lines.append("")

    # Rest — brief titles only
    rest = digest.get("rest", [])
    if rest:
        lines.append("▸▸▸ Также в новостях ▸▸▸")
        for title in rest[:25]:
            lines.append(f"• {title}")
        lines.append("")

    # Footer
    lines.append(f"— {total} сюжетов / {total_raw} статей / {date_str}")

    return "\n".join(lines)


def main():
    if not os.path.exists(DIGEST_JSON):
        print("ERROR: digest.json not found", file=__import__("sys").stderr)
        raise SystemExit(1)

    data = load_digest(DIGEST_JSON)
    output = format_digest(data)
    print(output)


if __name__ == "__main__":
    main()
