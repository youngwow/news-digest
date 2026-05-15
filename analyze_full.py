#!/usr/bin/env python3
"""
AI-driven digest assembler — no regex, no hardcoded rules.
All classification, dedup, and summarization is done by LLM subagents
via delegate_task. This script only handles data I/O and final assembly.

Phase 1: Load raw_news.json → write articles.json (flat list for subagent)
Phase 2: (subagent) Classify + deduplicate → writes classified.json
Phase 3: (subagent) Summarize each story → writes summarized.json
Phase 4: Assemble digest.json + digest.md from AI outputs
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_NEWS       = os.path.join(HERE, "raw_news.json")
ARTICLES_JSON  = os.path.join(HERE, "articles.json")
CLASSIFIED_JSON = os.path.join(HERE, "classified.json")
SUMMARIZED_JSON = os.path.join(HERE, "summarized.json")
DIGEST_JSON    = os.path.join(HERE, "digest.json")
DIGEST_MD      = os.path.join(HERE, "digest.md")
CHUNKS_DIR     = os.path.join(HERE, "chunks")
CHUNK_SIZE     = 15  # max articles per chunk for subagent processing

CATEGORY_EMOJI = {
    "политика": "🏛️", "экономика": "💰", "технологии": "🤖",
    "мир": "🌍", "спорт": "⚽", "наука": "🔬", "культура": "🎭", "прочее": "📌",
}

CAT_ORDER = ["политика", "мир", "экономика", "технологии", "спорт", "наука", "культура", "прочее"]


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
    print(f"Extracted {len(flat)} articles → {ARTICLES_JSON}")
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
        print(f"Chunk {idx}/{len(chunks)}: {len(chunk)} articles → {path}")

    print(f"Split {total} articles into {len(chunks)} chunks")
    return chunk_files


def merge_chunks():
    """Merge classified chunks into classified.json with cross-chunk dedup instructions."""
    all_stories = []
    seen_titles = set()

    for i in range(1, 10):  # up to 9 chunks
        path = os.path.join(CHUNKS_DIR, f"chunk_{i}_classified.json")
        input_path = os.path.join(CHUNKS_DIR, f"chunk_{i}.json")
        if not os.path.exists(path) or not os.path.exists(input_path):
            break
        data = load_json(path)
        for story in data.get("stories", []):
            tid = story.get("title", "").strip().lower()
            if tid and tid not in seen_titles:
                all_stories.append(story)
                seen_titles.add(tid)

    save_json(CLASSIFIED_JSON, {
        "classified_at": datetime.now(timezone.utc).isoformat(),
        "input_count": sum(len(load_json(os.path.join(CHUNKS_DIR, f"chunk_{i}.json"))["articles"])
                           for i in range(1, 10)
                           if os.path.exists(os.path.join(CHUNKS_DIR, f"chunk_{i}.json"))),
        "stories": all_stories,
    })
    print(f"Merged chunks → {len(all_stories)} stories in {CLASSIFIED_JSON}")
    return CLASSIFIED_JSON


def cleanup_chunks():
    """Remove chunk files after successful digest generation."""
    if not os.path.isdir(CHUNKS_DIR):
        return
    import shutil
    shutil.rmtree(CHUNKS_DIR)
    print(f"Cleaned up {CHUNKS_DIR}")


def validate_classified(path=CLASSIFIED_JSON):
    """Check that classified.json has required structure."""
    data = load_json(path)
    stories = data.get("stories", [])
    for s in stories:
        assert s.get("title"), "story missing title"
        assert s.get("category") in list(CATEGORY_EMOJI.keys()) + ["прочее"], f"invalid category: {s.get('category')}"
        assert isinstance(s.get("importance"), int), "importance must be int"
        assert isinstance(s.get("article_indexes", []), list), "article_indexes must be list"
    print(f"Validated: {len(stories)} stories in {path}")
    return data


def validate_summarized(path=SUMMARIZED_JSON):
    """Check that summarized.json has required structure."""
    data = load_json(path)
    stories = data.get("stories", [])
    for s in stories:
        assert s.get("title"), "story missing title"
        assert s.get("category"), "story missing category"
        assert s.get("short_summary"), "story missing short_summary"
        assert isinstance(s.get("importance"), int), "importance must be int"
    print(f"Validated: {len(stories)} stories in {path}")
    return data


def assemble_digest():
    """Read classified stories → build digest.json + digest.md (titles only, no summaries)."""
    data = load_json(CLASSIFIED_JSON)
    stories = data.get("stories", [])

    if not stories:
        print("ERROR: no stories to assemble")
        sys.exit(1)

    # Sort by importance desc
    stories.sort(key=lambda s: (s.get("importance", 5), len(s.get("title", ""))), reverse=True)

    headline = stories[0]["title"]
    top5 = stories[:5]

    # By category
    by_cat = defaultdict(list)
    for s in stories:
        by_cat[s.get("category", "прочее")].append(s)

    rubrics = {}
    for cat in CAT_ORDER:
        items = by_cat.get(cat, [])[:5]
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

    # Date — most common publish date
    date_counts = Counter()
    for s in stories:
        pub = s.get("published")
        if pub:
            date_counts[pub[:10]] += 1
    if date_counts:
        iso = date_counts.most_common(1)[0][0]
        parts = iso.split("-")
        date_str = f"{parts[2]}.{parts[1]}.{parts[0]}"
    else:
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
    print(f"Saved {DIGEST_JSON}")

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
    print(f"Saved {DIGEST_MD} ({len(md)} chars)")

    # Stats
    cat_counts = Counter(s.get("category", "прочее") for s in stories)
    print("\nCategory distribution:")
    for cat, cnt in cat_counts.most_common():
        print(f"  {cat}: {cnt}")

    return full


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI-driven digest assembler")
    parser.add_argument("--phase", choices=["extract", "split", "merge", "cleanup", "validate-classified", "validate-summarized", "assemble"],
                        default="assemble")
    args = parser.parse_args()

    if args.phase == "extract":
        extract_flat_articles()
    elif args.phase == "split":
        split_into_chunks()
    elif args.phase == "merge":
        merge_chunks()
    elif args.phase == "cleanup":
        cleanup_chunks()
    elif args.phase == "validate-classified":
        validate_classified()
    elif args.phase == "validate-summarized":
        validate_summarized()
    elif args.phase == "assemble":
        assemble_digest()
    else:
        pass


if __name__ == "__main__":
    main()
