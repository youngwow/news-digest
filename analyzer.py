#!/usr/bin/env python3
"""
Minimal data I/O and rendering for the news digest pipeline.
All AI work (classification, dedup, summarization) is done by
LLM subagents via delegate_task. This script handles only:
- Loading/saving JSON files
- Rendering digest.json → digest.md (markdown formatting)
"""

import json
import logging
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))

DIGEST_JSON = os.path.join(HERE, "digest.json")
DIGEST_MD   = os.path.join(HERE, "digest.md")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("analyzer")


def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def phase2_render(digest: dict, merged: list[dict]) -> str:
    """Render digest dict as readable markdown."""
    rubric_emoji = {
        "политика": "🏛️", "экономика": "💰", "технологии": "🤖",
        "мир": "🌍", "спорт": "⚽", "наука": "🔬", "культура": "🎭", "прочее": "📌",
    }

    lines = [
        f"# 📰 Дайджест новостей — {digest.get('date', 'сегодня')}",
        "",
        f"## 🔥 Заголовок дня",
        f"**{digest.get('headline', '')}**",
        "",
        "---",
        "## 🏆 Топ-5 главных новостей",
        "",
    ]

    for i, item in enumerate(digest.get("top5", []), 1):
        lines.append(f"### {i}. {item.get('title', '')}")
        lines.append(item.get("body", ""))
        lines.append("")

    lines += ["---", "", "## 📂 Рубрики", ""]

    for cat_name, items in digest.get("rubrics", {}).items():
        emoji = rubric_emoji.get(cat_name, "📌")
        lines.append(f"### {emoji} {cat_name.capitalize()}")
        lines.append("")
        for item in items[:8]:
            lines.append(f"- **{item.get('title', '')}** — {item.get('body', '')}")
        lines.append("")

    lines += ["---", "", "## 📋 Остальные новости кратко", ""]

    for title in digest.get("rest", [])[:40]:
        lines.append(f"- {title}")

    lines += [
        "",
        "---",
        f"*Сгенерировано AI: {datetime.now().strftime('%d.%m.%Y %H:%M')} MSK*",
        f"*Проанализировано: {len(merged)} сюжетов / {sum(a.get('group_size', 1) for a in merged)} статей*",
    ]
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="News digest markdown renderer")
    parser.add_argument("--digest", type=str, help="Path to digest JSON")
    args = parser.parse_args()

    digest_path = args.digest or DIGEST_JSON
    if not os.path.exists(digest_path):
        log.error("Digest file not found: %s", digest_path)
        sys.exit(1)

    digest_data = load_json(digest_path)
    digest = digest_data.get("digest", digest_data)
    merged = digest_data.get("all_stories", [])

    md = phase2_render(digest, merged)
    with open(DIGEST_MD, "w", encoding="utf-8") as f:
        f.write(md)
    log.info("Saved digest.md (%d chars)", len(md))
    print(f"DONE: digest.md written ({len(md)} chars)")


if __name__ == "__main__":
    main()
