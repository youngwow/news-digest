#!/usr/bin/env python3
"""
AI-driven digest assembler — extract, split, validate, and assemble phases.
Reads raw_news.json / classified.json and writes articles.json, chunks/, and digest.{json,md}.
"""

import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

from utils import CONFIG, DATA_DIR, get_logger, load_json, save_json

log = get_logger("analyze_full")

RAW_NEWS        = os.path.join(DATA_DIR, "raw_news.json")
ARTICLES_JSON   = os.path.join(DATA_DIR, "articles.json")
CLASSIFIED_JSON = os.path.join(DATA_DIR, "classified.json")
DIGEST_JSON     = os.path.join(DATA_DIR, "digest.json")
DIGEST_MD       = os.path.join(DATA_DIR, "digest.md")
CHUNKS_DIR      = os.path.join(DATA_DIR, "chunks")

_pipeline      = CONFIG["pipeline"]
_cats          = CONFIG["categories"]
CHUNK_SIZE     = _pipeline["chunk_size"]
CATEGORY_EMOJI = _cats["emoji"]
CAT_ORDER      = _cats["order"]


def extract_flat_articles():
    """Load raw_news.json → extract flat article list for subagent consumption."""
    data = load_json(RAW_NEWS)
    articles = data.get("articles", [])
    flat = []
    for a in articles:
        flat.append({
            "title": a.get("title", "").strip(),
            "summary": a.get("summary", "").strip(),
            "published": a.get("published"),
            "source": a.get("source", ""),
            "url": a.get("url", ""),
        })
    save_json(ARTICLES_JSON, {
        "collected_at": data.get("collected_at"),
        "total": len(flat),
        "articles": flat,
    })
    log.info("Extracted %d articles → %s", len(flat), ARTICLES_JSON)
    return flat


def split_into_chunks():
    """Split articles.json into CHUNK_SIZE-sized chunks for parallel subagent processing."""
    os.makedirs(CHUNKS_DIR, exist_ok=True)
    data = load_json(ARTICLES_JSON)
    articles = data.get("articles", [])
    total = len(articles)
    chunks = [articles[i:i + CHUNK_SIZE] for i in range(0, total, CHUNK_SIZE)]

    chunk_files = []
    for idx, chunk in enumerate(chunks, 1):
        path = os.path.join(CHUNKS_DIR, f"chunk_{idx}.json")
        save_json(path, {
            "chunk_index": idx,
            "total_chunks": len(chunks),
            "articles": chunk,
        })
        chunk_files.append(path)
        log.info("Chunk %d/%d: %d articles → %s", idx, len(chunks), len(chunk), path)

    log.info("Split %d articles into %d chunks", total, len(chunks))
    return chunk_files



def cleanup_chunks():
    """Remove chunk files after successful digest generation."""
    if not os.path.isdir(CHUNKS_DIR):
        return
    import shutil
    shutil.rmtree(CHUNKS_DIR)
    log.info("Cleaned up %s", CHUNKS_DIR)


def validate_classified(path=CLASSIFIED_JSON):
    """Check that classified.json has required structure."""
    data = load_json(path)
    stories = data.get("stories", [])
    for s in stories:
        assert s.get("title"), "story missing title"
        assert s.get("category") in CATEGORY_EMOJI, f"invalid category: {s.get('category')}"
        assert isinstance(s.get("importance"), int), "importance must be int"
    log.info("Validated: %d stories in %s", len(stories), path)
    return data


def assemble_digest():
    """Read classified stories → build digest.json + digest.md (titles only, no summaries)."""
    data = load_json(CLASSIFIED_JSON)
    stories = data.get("stories", [])

    if not stories:
        log.error("no stories to assemble")
        sys.exit(1)

    # Sort by importance desc
    stories.sort(key=lambda s: (s.get("importance", 5), len(s.get("title", ""))), reverse=True)

    headline = stories[0]["title"]
    top5 = stories[:5]
    top5_titles = {s["title"].strip().lower() for s in top5}

    # By category
    by_cat = defaultdict(list)
    for s in stories:
        by_cat[s.get("category", "прочее")].append(s)

    rubrics = {}
    for cat in CAT_ORDER:
        # Exclude stories already shown in the top-5 section
        items = [s for s in by_cat.get(cat, []) if s["title"].strip().lower() not in top5_titles][:5]
        if items:
            rubrics[cat] = [{"title": s["title"]} for s in items]

    # Rest — exclude titles already in top5 and rubrics
    used_titles = set()
    for s in top5:
        used_titles.add(s["title"].strip().lower())
    for items in rubrics.values():
        for item in items:
            used_titles.add(item["title"].strip().lower())

    rest_titles = []
    for s in stories[5:]:
        t = s["title"].strip()
        if t.lower() not in used_titles:
            rest_titles.append(t)
            used_titles.add(t.lower())

    # Date — derived from pipeline run time (classified stories have no published field)
    classified_at = data.get("classified_at", "")
    try:
        dt = datetime.fromisoformat(classified_at.replace("Z", "+00:00"))
        date_str = dt.strftime("%d.%m.%Y")
    except (ValueError, TypeError, AttributeError):
        date_str = datetime.now(timezone.utc).strftime("%d.%m.%Y")

    digest = {
        "headline": headline,
        "date": date_str,
        "top5": [{"title": s["title"]} for s in top5],
        "rubrics": rubrics,
        "rest": rest_titles,
    }

    full = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": RAW_NEWS,
        "total_raw": data.get("input_count", len(stories)),
        "total_unique": len(stories),
        "digest": digest,
        "all_stories": stories,
    }

    save_json(DIGEST_JSON, full)
    log.info("Saved %s", DIGEST_JSON)

    # Markdown — titles only, no summaries
    lines = [
        f"# 📰 Дайджест новостей — {date_str}",
        "",
        "## 🔥 Заголовок дня",
        f"**{headline}**",
        "",
        "---",
        "## 🏆 Топ-5 главных новостей",
        "",
    ]
    for i, s in enumerate(top5, 1):
        lines.append(f"### {i}. {s['title']}")
        lines.append("")

    lines += ["---", "", "## 📂 Рубрики", ""]
    for cat in CAT_ORDER:
        if cat in rubrics:
            emoji = CATEGORY_EMOJI.get(cat, "📌")
            lines.append(f"### {emoji} {cat.capitalize()}")
            lines.append("")
            for item in rubrics[cat]:
                lines.append(f"- {item['title']}")
            lines.append("")

    lines += ["---", "", "## 📋 Остальные новости кратко", ""]
    for t in rest_titles[:50]:
        lines.append(f"- {t}")

    lines += [
        "",
        "---",
        f"*Сгенерировано AI: {datetime.now().strftime('%d.%m.%Y %H:%M')} MSK*",
        f"*Проанализировано: {len(stories)} сюжетов / {data.get('input_count', '?')} статей*",
    ]

    md = "\n".join(lines)
    with open(DIGEST_MD, "w", encoding="utf-8") as f:
        f.write(md)
    log.info("Saved %s (%d chars)", DIGEST_MD, len(md))

    cat_counts = Counter(s.get("category", "прочее") for s in stories)
    log.info("Category distribution: %s", ", ".join(f"{c}={n}" for c, n in cat_counts.most_common()))

    return full


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI-driven digest assembler")
    parser.add_argument("--phase", choices=["extract", "split", "cleanup", "validate-classified", "assemble"],
                        default="assemble")
    args = parser.parse_args()

    if args.phase == "extract":
        extract_flat_articles()
    elif args.phase == "split":
        split_into_chunks()
    elif args.phase == "cleanup":
        cleanup_chunks()
    elif args.phase == "validate-classified":
        validate_classified()
    elif args.phase == "assemble":
        assemble_digest()


if __name__ == "__main__":
    main()
