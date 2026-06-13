#!/usr/bin/env python3
"""
AI-driven digest assembler — extract, split, validate, and assemble phases.
Reads raw_news.json / classified.json and writes articles.json, chunks/, and digest.{json,md}.
"""

import os
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from utils import CONFIG, DATA_DIR, get_logger, load_json, mark_urls_seen, pluralize_ru, save_json

log = get_logger("analyze_full")

RAW_NEWS        = os.path.join(DATA_DIR, "raw_news.json")
ARTICLES_JSON   = os.path.join(DATA_DIR, "articles.json")
CLASSIFIED_JSON = os.path.join(DATA_DIR, "classified.json")
DIGEST_JSON     = os.path.join(DATA_DIR, "digest.json")
DIGEST_MD       = os.path.join(DATA_DIR, "digest.md")
CHUNKS_DIR      = os.path.join(DATA_DIR, "chunks")
DIGESTS_DIR     = os.path.join(DATA_DIR, "digests")

_pipeline      = CONFIG["pipeline"]
_cats          = CONFIG["categories"]
CHUNK_SIZE     = _pipeline["chunk_size"]
CATEGORY_EMOJI = _cats["emoji"]
CAT_ORDER      = _cats["order"]
ARCHIVE_RETENTION_DAYS    = CONFIG["archive"]["retention_days"]
CROSS_RUN_RETENTION_DAYS  = CONFIG["dedup"]["cross_run_retention_days"]


def _archive_digest(full: dict) -> None:
    """Write a timestamped snapshot under data/digests/, prune entries older than retention."""
    os.makedirs(DIGESTS_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
    snapshot_path = os.path.join(DIGESTS_DIR, f"digest-{ts}.json")
    save_json(snapshot_path, full)
    log.info("Archived digest snapshot → %s", snapshot_path)

    cutoff = time.time() - ARCHIVE_RETENTION_DAYS * 86400
    removed = 0
    for entry in os.listdir(DIGESTS_DIR):
        path = os.path.join(DIGESTS_DIR, entry)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    if removed:
        log.info("digests: pruned %d snapshots older than %d days",
                 removed, ARCHIVE_RETENTION_DAYS)


def _record_delivered_urls(full: dict) -> None:
    """Mark every URL across all_stories as seen so future runs can dedup against it."""
    urls = []
    for story in full.get("all_stories", []):
        urls.extend(story.get("urls", []))
    if urls:
        mark_urls_seen(urls, CROSS_RUN_RETENTION_DAYS)
        log.info("Marked %d URLs as seen (retention: %d days)",
                 len(urls), CROSS_RUN_RETENTION_DAYS)


def extract_flat_articles() -> list[dict]:
    """Load raw_news.json → extract flat article list for subagent consumption."""
    data = load_json(RAW_NEWS)
    articles = data.get("articles", [])
    flat = []
    for a in articles:
        entry = {
            "title": a.get("title", "").strip(),
            "summary": a.get("summary", "").strip(),
            "published": a.get("published"),
            "source": a.get("source", ""),
            "url": a.get("url", ""),
        }
        body = a.get("body", "").strip()
        if body:
            entry["body"] = body
        flat.append(entry)
    save_json(ARTICLES_JSON, {
        "collected_at": data.get("collected_at"),
        "total": len(flat),
        "articles": flat,
    })
    log.info("Extracted %d articles → %s", len(flat), ARTICLES_JSON)
    return flat


def split_into_chunks() -> list[str]:
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



def cleanup_chunks() -> None:
    """Remove chunk files after successful digest generation."""
    if not os.path.isdir(CHUNKS_DIR):
        return
    shutil.rmtree(CHUNKS_DIR)
    log.info("Cleaned up %s", CHUNKS_DIR)


def validate_classified(path: str = CLASSIFIED_JSON) -> dict:
    """Check that classified.json has required structure.

    Uses explicit checks (not asserts — those vanish under `python -O`) and
    reports every offending story instead of stopping at the first.
    """
    data = load_json(path)
    stories = data.get("stories", [])
    problems: list[str] = []
    for i, s in enumerate(stories):
        if not s.get("title"):
            problems.append(f"story[{i}]: missing title")
        if s.get("category") not in CATEGORY_EMOJI:
            problems.append(f"story[{i}]: invalid category: {s.get('category')!r}")
        if not isinstance(s.get("importance"), int):
            problems.append(
                f"story[{i}]: importance must be int, got {type(s.get('importance')).__name__}")
    if problems:
        for p in problems:
            log.error(p)
        raise SystemExit(f"{path}: {len(problems)} validation error(s)")
    log.info("Validated: %d stories in %s", len(stories), path)
    return data


def build_digest(stories: list[dict], classified_at: str = "") -> dict:
    """Pure transform: stories → digest dict (headline / top5 / rubrics / rest).

    Sorts `stories` in place by importance (desc, longer title breaks ties).
    Requires a non-empty list. Shared by the assemble phase and weekly_rollup.py.
    """
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
            entries = []
            for s in items:
                e = {"title": s["title"]}
                if s.get("follow_up_of"):
                    e["thread"] = True
                entries.append(e)
            rubrics[cat] = entries

    # Rest — exclude titles already in top5 and rubrics
    used_titles = {s["title"].strip().lower() for s in top5}
    for items in rubrics.values():
        for item in items:
            used_titles.add(item["title"].strip().lower())

    rest_titles = []
    for s in stories[5:]:
        t = s["title"].strip()
        if t.lower() not in used_titles:
            rest_titles.append(t)
            used_titles.add(t.lower())

    # Date — derived from pipeline run time (classified stories have no published
    # field), rendered in local time so a post-midnight run stamps the right day
    try:
        dt = datetime.fromisoformat(classified_at.replace("Z", "+00:00"))
        date_str = dt.astimezone().strftime("%d.%m.%Y")
    except (ValueError, TypeError, AttributeError):
        date_str = datetime.now().astimezone().strftime("%d.%m.%Y")

    # Top-5 carries the primary URL and the LLM's one-sentence summary
    top5_entries = []
    for s in top5:
        entry = {"title": s["title"]}
        if s.get("follow_up_of"):
            entry["thread"] = True
        summary = (s.get("short_summary") or "").strip()
        if summary:
            entry["summary"] = summary
        urls = s.get("urls") or []
        if urls:
            entry["url"] = urls[0]
        top5_entries.append(entry)

    return {
        "headline": headline,
        "date": date_str,
        "top5": top5_entries,
        "rubrics": rubrics,
        "rest": rest_titles,
    }


def render_markdown(digest: dict, total_stories: int, input_count) -> str:
    """Render a digest dict as the digest.md document (titles only, no summaries)."""
    lines = [
        f"# 📰 Дайджест новостей — {digest['date']}",
        "",
        "## 🔥 Заголовок дня",
        f"**{digest['headline']}**",
        "",
        "---",
        "## 🏆 Топ-5 главных новостей",
        "",
    ]
    for i, item in enumerate(digest["top5"], 1):
        mark = "🔄 " if item.get("thread") else ""
        lines.append(f"### {i}. {mark}{item['title']}")
        if item.get("summary"):
            lines.append(item["summary"])
        if item.get("url"):
            lines.append(f"<{item['url']}>")
        lines.append("")

    lines += ["---", "", "## 📂 Рубрики", ""]
    rubrics = digest["rubrics"]
    for cat in CAT_ORDER:
        if cat in rubrics:
            emoji = CATEGORY_EMOJI.get(cat, "📌")
            lines.append(f"### {emoji} {cat.capitalize()}")
            lines.append("")
            for item in rubrics[cat]:
                mark = "🔄 " if item.get("thread") else ""
                lines.append(f"- {mark}{item['title']}")
            lines.append("")

    lines += ["---", "", "## 📋 Остальные новости кратко", ""]
    for t in digest["rest"][:50]:
        lines.append(f"- {t}")

    stories_word = pluralize_ru(total_stories, "сюжет", "сюжета", "сюжетов")
    articles_word = (pluralize_ru(input_count, "статья", "статьи", "статей")
                     if isinstance(input_count, int) else "статей")
    lines += [
        "",
        "---",
        f"*Сгенерировано AI: {datetime.now().strftime('%d.%m.%Y %H:%M')} MSK*",
        f"*Проанализировано: {total_stories} {stories_word} / {input_count} {articles_word}*",
    ]
    return "\n".join(lines)


def assemble_digest() -> dict:
    """Read classified stories → build digest.json + digest.md (titles only, no summaries)."""
    data = load_json(CLASSIFIED_JSON)
    stories = data.get("stories", [])

    if not stories:
        log.error("no stories to assemble")
        sys.exit(1)

    # Mark follow-ups of recently-delivered stories. Runs before _archive_digest
    # so a story never threads against its own snapshot. Lazy import: the module
    # pulls in weekly_rollup, which imports back into analyze_full.
    from story_threads import annotate_from_archive
    annotate_from_archive(stories)

    digest = build_digest(stories, data.get("classified_at", ""))

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

    _archive_digest(full)
    _record_delivered_urls(full)

    md = render_markdown(digest, len(stories), data.get("input_count", "?"))
    with open(DIGEST_MD, "w", encoding="utf-8") as f:
        f.write(md)
    log.info("Saved %s (%d chars)", DIGEST_MD, len(md))

    cat_counts = Counter(s.get("category", "прочее") for s in stories)
    log.info("Category distribution: %s", ", ".join(f"{c}={n}" for c, n in cat_counts.most_common()))

    return full


def main() -> None:
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
