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


RUSSIAN_STOPWORDS: set[str] = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "но", "его", "по", "из", "у", "же", "за", "от", "о",
    "бы", "для", "это", "или", "ее", "до", "мы", "их", "был", "еще", "к",
    "когда", "да", "вы", "при", "без", "под", "нет", "ли", "там", "где",
    "уже", "если", "очень", "быть", "себя", "тут", "над", "чем", "кто",
    "этот", "тому", "будет", "после", "один", "может", "теперь", "более",
    "сейчас", "через", "весь", "того", "пока", "них", "здесь", "этой",
    "почти", "около", "также", "среди", "этих", "между", "можно", "были",
    "также", "только", "более", "назад",
}

COMMON_MODIFIERS: set[str] = {
    "сообщил", "сообщила", "сообщили", "заявил", "заявила", "заявили",
    "рассказал", "рассказала", "рассказали", "отметил", "отметила", "отметили",
    "пишет", "написал", "написала", "написали", "новый", "новая", "новые",
    "нового", "новых", "снова", "опять", "главный", "главная", "главные",
    "стало", "известно", "появилось", "появились",
}

def _russian_stem(word: str) -> str:
    """Light Russian stemmer: strip common noun/adjective endings."""
    w = word
    # Strip common endings greedily
    for suffix in ("ами", "ями", "ах", "ях", "ого", "его", "ому", "ему", "ую",
                   "ого", "ыми", "ими", "ой", "ей", "ый", "ий", "ая", "яя", "ое",
                   "ее", "ые", "ие", "ом", "ем", "ам", "ям", "а", "я", "ы", "и",
                   "у", "ю", "е", "о"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[:-len(suffix)]
    return w


SIGNIFICANT_WORDS_BLACKLIST: set[str] = RUSSIAN_STOPWORDS | COMMON_MODIFIERS

def _get_significant_words(title: str) -> set[str]:
    """Extract significant words from a title, stripping stopwords, modifiers, and stemming."""
    words = normalize_title(title).split()
    return {_russian_stem(w) for w in words
            if w not in SIGNIFICANT_WORDS_BLACKLIST and len(w) > 1}


def _jaccard(s1: set[str], s2: set[str]) -> float:
    """Jaccard similarity coefficient."""
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


def _overlap_ratio(s1: set[str], s2: set[str]) -> float:
    """Fraction of the smaller set's words that appear in the larger set."""
    if not s1 or not s2:
        return 0.0
    smaller, larger = (s1, s2) if len(s1) <= len(s2) else (s2, s1)
    return len(smaller & larger) / len(smaller)


def dedup_articles(articles: list[dict]) -> list[dict]:
    """
    Two-pass deduplication:
    1. Group by first 6 normalized words of title (prefix match).
    2. Merge groups with high semantic overlap (Jaccard > 0.5 or >70% shared words).
    Returns one entry per group: longest summary wins, all sources merged.
    """
    # --- Pass 1: prefix grouping ------------------------------------------------
    groups: dict[str, list[int]] = defaultdict(list)

    for i, art in enumerate(articles):
        words = normalize_title(art["title"]).split()[:6]
        key = " ".join(words) if len(words) >= 2 else normalize_title(art["title"])[:60]
        groups[key].append(i)

    # Build intermediate merged list
    merged: list[dict] = []
    for key, indices in groups.items():
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

    pass1_count = len(merged)
    log.info("Pass 1: %d articles → %d groups (prefix match)", len(articles), pass1_count)

    # --- Pass 2: semantic merge -------------------------------------------------
    if len(merged) <= 1:
        log.info("Pass 2: only %d group(s), nothing to merge", len(merged))
        return merged

    # Pre-compute significant-word sets for every group
    sig_sets = [_get_significant_words(g["title"]) for g in merged]

    # Union-Find over group indices
    parent = list(range(len(merged)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(merged)):
        for j in range(i + 1, len(merged)):
            si, sj = sig_sets[i], sig_sets[j]
            if not si or not sj:
                continue
            jac = _jaccard(si, sj)
            ovr = _overlap_ratio(si, sj)
            shared = len(si & sj)
            # Merge if: weak Jaccard, moderate overlap, or ≥2 shared significant words
            if jac > 0.15 or ovr > 0.3 or shared >= 2:
                union(i, j)

    # Collect merged clusters
    clusters: defaultdict[int, list[int]] = defaultdict(list)
    for i in range(len(merged)):
        clusters[find(i)].append(i)

    merged2: list[dict] = []
    merges_logged: list[str] = []

    for root, indices in clusters.items():
        if len(indices) == 1:
            merged2.append(merged[indices[0]])
            continue

        # Merge cluster: pick best (longest summary) as canonical
        best_idx = max(indices, key=lambda i: len(merged[i].get("summary", "")))
        art = merged[best_idx]
        all_sources = sorted(set(src for i in indices for src in merged[i]["sources"]))
        all_urls = [url for i in indices for url in merged[i]["urls"]]
        total_group_size = sum(merged[i]["group_size"] for i in indices)

        merged2.append({
            "title":    art["title"],
            "summary":  art.get("summary", ""),
            "published": art.get("published"),
            "sources":  all_sources,
            "urls":     all_urls,
            "group_size": total_group_size,
            "category": None,
            "importance": None,
            "entities": [],
            "short_summary": "",
        })

        titles_in_cluster = " | ".join(merged[i]["title"] for i in indices)
        merges_logged.append(f"  merged {len(indices)} groups: [{titles_in_cluster}]")

    pass2_count = len(merged2)
    if merges_logged:
        log.info("Pass 2: %d groups merged → %d groups", pass1_count, pass2_count)
        for line in merges_logged:
            log.info(line)
    else:
        log.info("Pass 2: no semantic matches found (still %d groups)", pass2_count)

    log.info("Deduplicated %d articles → %d groups", len(articles), pass2_count)
    return merged2


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
