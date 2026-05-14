#!/usr/bin/env python3
"""
News analyzer & digest pipeline — data I/O, dedup, formatting.
The AI analysis is done by the Hermes agent itself (not via subprocess);
this script handles the mechanical pipeline.

Phase 1: Load raw_news.json, deduplicate by title similarity.
Phase 2: Output analyzed_news.json (structure for the agent to fill in).
Phase 3: Read agent-filled analyses, generate digest.json + digest.md.
"""

import json
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

RAW_NEWS    = os.path.join(HERE, "raw_news.json")
ANALYZED    = os.path.join(HERE, "analyzed_news.json")
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


def normalize_title(title: str) -> str:
    """Lowercase, remove punctuation, collapse whitespace."""
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def dedup_articles(articles: list[dict]) -> list[dict]:
    """
    Group by first 6 normalized words of title.
    Returns one entry per group: highest-priority source wins, all sources merged.
    """
    groups: dict[str, list[int]] = defaultdict(list)

    for i, art in enumerate(articles):
        words = normalize_title(art["title"]).split()[:6]
        key = " ".join(words) if len(words) >= 2 else normalize_title(art["title"])[:60]
        groups[key].append(i)

    merged = []
    for key, indices in groups.items():
        # Pick article with the longest summary as canonical
        best_idx = max(indices, key=lambda i: len(articles[i].get("summary", "")))
        art = articles[best_idx]
        sources = sorted(set(articles[i]["source"] for i in indices))
        urls    = [articles[i]["url"] for i in indices]

        merged.append({
            "title":    art["title"],
            "summary":  art.get("summary", ""),
            "published": art.get("published"),
            "sources":  sources,
            "urls":     urls,
            "group_size": len(indices),
            # To be filled by AI:
            "category": None,
            "importance": None,
            "entities": [],
            "short_summary": "",
        })

    log.info("Deduplicated %d articles → %d groups", len(articles), len(merged))
    return merged


def phase1_dedup() -> list[dict]:
    """Load raw_news, deduplicate, save merged skeleton to analyzed_news.json."""
    data = load_json(RAW_NEWS)
    articles = data["articles"]
    log.info("Loaded %d articles from %s", len(articles), RAW_NEWS)

    merged = dedup_articles(articles)
    save_json(ANALYZED, {
        "source": RAW_NEWS,
        "input_count": len(articles),
        "group_count": len(merged),
        "deduped_at": datetime.now(timezone.utc).isoformat(),
        "articles": merged,
    })
    log.info("Saved %d groups to %s — ready for AI analysis", len(merged), ANALYZED)
    return merged


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
        f"*Сгенерировано: {datetime.now().strftime('%d.%m.%Y %H:%M')} MSK*",
        f"*Всего проанализировано: {len(merged)} уникальных сюжетов / {sum(a['group_size'] for a in merged)} статей*",
    ]
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="News digest pipeline")
    parser.add_argument("--phase", choices=["dedup", "render"], default="dedup",
                        help="Pipeline phase")
    parser.add_argument("--digest", type=str, help="Path to digest JSON (for render phase)")
    args = parser.parse_args()

    if args.phase == "dedup":
        merged = phase1_dedup()
        print(f"DONE: {len(merged)} groups ready for AI analysis in {ANALYZED}")

    elif args.phase == "render":
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
