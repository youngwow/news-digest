#!/usr/bin/env python3
"""
Mechanical chunk merger with aggressive cross-chunk dedup.
Reads chunk_N_classified.json from chunks/ → deduplicates within categories → writes classified.json.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from utils import CONFIG, DATA_DIR, get_logger

CHUNKS_DIR = os.path.join(DATA_DIR, "chunks")
ARTICLES_JSON = os.path.join(DATA_DIR, "articles.json")
SOURCES_FILE  = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "sources.json")

_dedup = CONFIG["dedup"]
OVERLAP_THRESHOLD   = _dedup["overlap_threshold"]
SHARED_WORDS_MIN    = _dedup["shared_words_min"]
CONTAINMENT_MIN_LEN = _dedup["containment_min_len"]

_heur = CONFIG["heuristics"]
MULTI_SOURCE_MAX_BOOST   = _heur["multi_source_max_boost"]
RECENCY_WINDOW_HOURS     = _heur["recency_window_hours"]
RECENCY_BOOST            = _heur["recency_boost"]
SOURCE_WEIGHT_MAX_BOOST  = _heur["source_weight_max_boost"]

log = get_logger("merge_chunks")


def title_words(title: str) -> set[str]:
    """Extract significant words from title (no regex)."""
    t = title.lower()
    for ch in ".,!?;:—«»\"'()[]{}…":
        t = t.replace(ch, " ")
    words = t.split()
    stop = {"и","в","во","не","что","он","на","я","с","со","как","а","то","все",
            "она","так","но","его","по","из","у","же","за","от","о","бы","для",
            "это","или","до","мы","их","был","еще","к","когда","да","вы","при",
            "без","под","нет","ли","там","где","уже","если","быть","себя","этот",
            "будет","после","может","теперь","сейчас","через","пока","них","здесь",
            "также","только","сообщил","заявил","рассказал","пишет","написал",
            "новый","стало","известно","очень"}
    return {w for w in words if len(w) > 2 and w not in stop}


def merge_stories(s1: dict, s2: dict) -> dict:
    """Merge s2 into s1 (in-place)."""
    s1["sources"] = sorted(set(s1.get("sources", []) + s2.get("sources", [])))
    s1["urls"] = list(dict.fromkeys(s1.get("urls", []) + s2.get("urls", [])))
    s1["importance"] = max(s1.get("importance", 5), s2.get("importance", 5))
    if len(s2.get("title", "")) > len(s1.get("title", "")):
        s1["title"] = s2["title"]
    if len(s2.get("short_summary", "")) > len(s1.get("short_summary", "")):
        s1["short_summary"] = s2["short_summary"]
    return s1


def load_url_published_map() -> dict[str, str]:
    """Build {url: published-ISO} from articles.json (best-effort, empty on miss)."""
    if not os.path.exists(ARTICLES_JSON):
        return {}
    try:
        with open(ARTICLES_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, str] = {}
    for a in data.get("articles", []):
        url = a.get("url")
        pub = a.get("published")
        if url and pub:
            out[url] = pub
    return out


def load_source_weights() -> dict[str, float]:
    """Read sources.json and return {source_name: weight} (default 1.0 if missing)."""
    if not os.path.exists(SOURCES_FILE):
        return {}
    try:
        with open(SOURCES_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return {s["name"]: float(s.get("weight", 1.0)) for s in data.get("sources", [])}


def boost_importance(
    story: dict,
    url_published: dict[str, str],
    source_weights: dict[str, float],
    now: datetime | None = None,
) -> int:
    """Apply multi-source / recency / source-weight heuristics. Returns the new importance (≤10)."""
    importance = int(story.get("importance", 5))

    # Multi-source boost — +1 per extra unique source, capped
    unique_sources = len(set(story.get("sources", [])))
    if unique_sources > 1:
        importance += min(unique_sources - 1, MULTI_SOURCE_MAX_BOOST)

    # Source-weight boost — +floor(max_weight - 1), capped
    if source_weights:
        weights = [source_weights.get(src, 1.0) for src in story.get("sources", [])]
        if weights:
            extra = int(max(weights) - 1.0)
            if extra > 0:
                importance += min(extra, SOURCE_WEIGHT_MAX_BOOST)

    # Recency boost — any underlying article published within window
    if RECENCY_BOOST and url_published:
        ref_now = now or datetime.now(timezone.utc)
        cutoff = ref_now - timedelta(hours=RECENCY_WINDOW_HOURS)
        for url in story.get("urls", []):
            pub_str = url_published.get(url)
            if not pub_str:
                continue
            try:
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            except (ValueError, TypeError, AttributeError):
                continue
            if pub_dt >= cutoff:
                importance += RECENCY_BOOST
                break

    return min(importance, 10)


def dedup_stories(stories: list[dict]) -> list[dict]:
    """
    Aggressive cross-chunk dedup within a flat list.
    Groups stories that mention the same event (paraphrased titles).
    """
    n = len(stories)
    words_sets = [title_words(s["title"]) for s in stories]
    used = [False] * n
    merged = []

    for i in range(n):
        if used[i]:
            continue
        story = dict(stories[i])
        wi = words_sets[i]
        ti = story["title"].lower().rstrip(".")

        for j in range(i + 1, n):
            if used[j]:
                continue
            wj = words_sets[j]
            tj = stories[j]["title"].lower().rstrip(".")

            if not wi or not wj:
                continue

            shared = wi & wj
            total = wi | wj
            overlap = len(shared) / len(total) if total else 0

            if (overlap > OVERLAP_THRESHOLD or len(shared) >= SHARED_WORDS_MIN
                    or (len(ti) > CONTAINMENT_MIN_LEN and ti in tj)
                    or (len(tj) > CONTAINMENT_MIN_LEN and tj in ti)):
                story = merge_stories(story, stories[j])
                used[j] = True

        merged.append(story)

    return merged


def main() -> None:
    all_stories: list[dict] = []
    for i in range(1, 20):
        path = os.path.join(CHUNKS_DIR, f"chunk_{i}_classified.json")
        input_path = os.path.join(CHUNKS_DIR, f"chunk_{i}.json")
        if not os.path.exists(input_path):
            break
        if not os.path.exists(path):
            log.warning("chunk_%d: SKIPPED (classified output missing — API failure)", i)
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        chunk_stories = data.get("stories", [])
        all_stories.extend(chunk_stories)
        log.info("chunk_%d: %d stories", i, len(chunk_stories))

    raw_count = len(all_stories)
    log.info("Raw merge: %d stories", raw_count)

    deduped = dedup_stories(all_stories)
    log.info("After dedup: %d stories (removed %d duplicates)", len(deduped), raw_count - len(deduped))

    # Heuristic importance boosts — multi-source, recency, source-weight
    url_published = load_url_published_map()
    source_weights = load_source_weights()
    boosted = 0
    for s in deduped:
        orig = int(s.get("importance", 5))
        new = boost_importance(s, url_published, source_weights)
        if new != orig:
            boosted += 1
        s["importance"] = new
    log.info("Heuristics: boosted importance on %d/%d stories", boosted, len(deduped))

    out_path = os.path.join(DATA_DIR, "classified.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "classified_at": datetime.now(timezone.utc).isoformat(),
            "input_count": raw_count,
            "stories": deduped,
        }, f, ensure_ascii=False, indent=2)

    log.info("Wrote %d stories → %s", len(deduped), out_path)


if __name__ == "__main__":
    main()
