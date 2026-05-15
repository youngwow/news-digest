#!/usr/bin/env python3
"""
Convert digest.json to compact Telegram-friendly markdown.
Output to stdout — designed for delivery as message body (no file).
Format: emoji separators, titles only (no summaries).
"""

import json
import os


HERE = os.path.dirname(os.path.abspath(__file__))
DIGEST_JSON = os.path.join(HERE, "digest.json")

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


def main():
    if not os.path.exists(DIGEST_JSON):
        print("ERROR: digest.json not found", file=__import__("sys").stderr)
        raise SystemExit(1)

    data = load_digest(DIGEST_JSON)
    output = format_digest(data)
    print(output)


if __name__ == "__main__":
    main()
