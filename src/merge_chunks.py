#!/usr/bin/env python3
"""
Mechanical chunk merger with aggressive cross-chunk dedup.
Reads chunk_N_classified.json from chunks/ → deduplicates across the whole story
list (category-agnostic word-overlap on titles) → writes classified.json.
"""
import glob
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Sequence

from training_log import append_training_rows
from utils import CONFIG, DATA_DIR, PROJECT_ROOT, get_logger, save_json

CHUNKS_DIR = os.path.join(DATA_DIR, "chunks")
ARTICLES_JSON = os.path.join(DATA_DIR, "articles.json")
SOURCES_FILE  = os.path.join(PROJECT_ROOT, "sources.json")

_dedup = CONFIG["dedup"]
OVERLAP_THRESHOLD   = _dedup["overlap_threshold"]
SHARED_WORDS_MIN    = _dedup["shared_words_min"]
CONTAINMENT_MIN_LEN = _dedup["containment_min_len"]
SEMANTIC_ENABLED      = _dedup["semantic_enabled"]
SEMANTIC_THRESHOLD    = float(_dedup["semantic_threshold"])
SEMANTIC_MODEL        = _dedup["semantic_model"]
SEMANTIC_MODEL_STATIC = _dedup["semantic_model_static"]

_heur = CONFIG["heuristics"]
MULTI_SOURCE_MAX_BOOST   = _heur["multi_source_max_boost"]
RECENCY_WINDOW_HOURS     = _heur["recency_window_hours"]
RECENCY_BOOST            = _heur["recency_boost"]
SOURCE_WEIGHT_MAX_BOOST  = _heur["source_weight_max_boost"]

log = get_logger("merge_chunks")


# Russian stop words (plus reporting verbs) ignored when comparing titles
_STOP_WORDS = frozenset({
    "и","в","во","не","что","он","на","я","с","со","как","а","то","все",
    "она","так","но","его","по","из","у","же","за","от","о","бы","для",
    "это","или","до","мы","их","был","еще","к","когда","да","вы","при",
    "без","под","нет","ли","там","где","уже","если","быть","себя","этот",
    "будет","после","может","теперь","сейчас","через","пока","них","здесь",
    "также","только","сообщил","заявил","рассказал","пишет","написал",
    "новый","стало","известно","очень",
})


def title_words(title: str) -> set[str]:
    """Extract significant words from title (no regex)."""
    t = title.lower()
    for ch in ".,!?;:—«»\"'()[]{}…":
        t = t.replace(ch, " ")
    return {w for w in t.split() if len(w) > 2 and w not in _STOP_WORDS}


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


# ── semantic dedup (embedding-based second pass) ────────────────────
#
# Word overlap misses Russian paraphrases («атаки дронов» / «удара беспилотника»),
# because it compares exact word forms. Sentence embeddings compare meaning.

def _normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Dot product of unit-norm vectors."""
    return sum(x * y for x, y in zip(a, b))


def _load_encoder() -> Callable[[list[str]], Sequence[Sequence[float]]] | None:
    """Return a texts→vectors callable from the first available backend, or None.

    Backend 1: sentence-transformers (best quality, requires a working torch).
    Backend 2: model2vec static embeddings (no torch, ~CPU-millisecond inference).
    Any load failure degrades gracefully to word-overlap-only dedup.
    """
    try:
        import torch
        from sentence_transformers import SentenceTransformer
        device = ("mps" if torch.backends.mps.is_available()
                  else "cuda" if torch.cuda.is_available()
                  else "cpu")
        model = SentenceTransformer(SEMANTIC_MODEL, device=device)
        log.info("semantic dedup: using sentence-transformers (%s) on %s",
                 SEMANTIC_MODEL, device)
        return lambda texts: model.encode(texts, normalize_embeddings=True)
    except Exception as e:  # noqa: BLE001 — ImportError, broken torch, download failure
        log.debug("sentence-transformers backend unavailable: %s", e)

    try:
        from model2vec import StaticModel
        model = StaticModel.from_pretrained(SEMANTIC_MODEL_STATIC)
        log.info("semantic dedup: using model2vec static embeddings (%s)", SEMANTIC_MODEL_STATIC)
        return lambda texts: model.encode(texts)
    except Exception as e:  # noqa: BLE001
        log.warning("semantic dedup disabled — no embedding backend available (%s); "
                    "install with: pip install -e '.[ml]'", e)
        return None


def dedup_semantic(
    stories: list[dict],
    threshold: float = SEMANTIC_THRESHOLD,
    encode: Callable[[list[str]], Sequence[Sequence[float]]] | None = None,
) -> list[dict]:
    """Merge stories whose title embeddings reach cosine similarity ≥ threshold.

    Same greedy grouping as dedup_stories. Returns the input unchanged when no
    embedding backend is available.
    """
    if len(stories) < 2:
        return stories
    if encode is None:
        encode = _load_encoder()
        if encode is None:
            return stories

    vecs = [_normalize(list(v)) for v in encode([s.get("title", "") for s in stories])]
    n = len(stories)
    used = [False] * n
    merged: list[dict] = []

    for i in range(n):
        if used[i]:
            continue
        story = dict(stories[i])
        for j in range(i + 1, n):
            if used[j]:
                continue
            sim = _cosine(vecs[i], vecs[j])
            if sim >= threshold:
                log.info("semantic merge (%.2f): %r + %r",
                         sim, story.get("title", "")[:60], stories[j].get("title", "")[:60])
                story = merge_stories(story, stories[j])
                used[j] = True
        merged.append(story)

    return merged


def _discover_chunk_indices() -> list[int]:
    """Return sorted indices of every chunk_N.json present in CHUNKS_DIR."""
    pattern = os.path.join(CHUNKS_DIR, "chunk_*.json")
    indices: list[int] = []
    for path in glob.glob(pattern):
        name = os.path.basename(path)
        m = re.fullmatch(r"chunk_(\d+)\.json", name)
        if m:
            indices.append(int(m.group(1)))
    return sorted(indices)


def main() -> None:
    all_stories: list[dict] = []
    for i in _discover_chunk_indices():
        path = os.path.join(CHUNKS_DIR, f"chunk_{i}_classified.json")
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

    if SEMANTIC_ENABLED:
        before = len(deduped)
        deduped = dedup_semantic(deduped)
        log.info("After semantic dedup: %d stories (removed %d paraphrase duplicates)",
                 len(deduped), before - len(deduped))

    # Distillation rows for a future local classifier — logged before the
    # heuristic boosts so importance is the raw LLM label
    append_training_rows(deduped)

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
    save_json(out_path, {
        "classified_at": datetime.now(timezone.utc).isoformat(),
        "input_count": raw_count,
        "stories": deduped,
    })

    log.info("Wrote %d stories → %s", len(deduped), out_path)


if __name__ == "__main__":
    main()
