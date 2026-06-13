#!/usr/bin/env python3
"""
Story threading: mark stories that develop a recently-delivered story.

Cross-run dedup works on exact URLs, so follow-up coverage (a new article on
yesterday's storyline) sails through and looks brand-new. This module compares
current story titles against archive snapshots from the last
`threads.lookback_days` days using the same embedding backend as semantic
dedup; a story whose best archive match reaches `threads.similarity_threshold`
gets `follow_up_of` set to the archived title, and renderers prefix it with 🔄.
"""

from __future__ import annotations

from typing import Callable, Sequence

from merge_chunks import _cosine, _load_encoder, _normalize
from utils import CONFIG, get_logger
from weekly_rollup import DIGESTS_DIR, collect_stories

_threads = CONFIG["threads"]
ENABLED = _threads["enabled"]
LOOKBACK_DAYS = _threads["lookback_days"]
SIMILARITY_THRESHOLD = float(_threads["similarity_threshold"])

log = get_logger("story_threads")


def annotate_followups(
    stories: list[dict],
    archive_stories: list[dict],
    threshold: float = SIMILARITY_THRESHOLD,
    encode: Callable[[list[str]], Sequence[Sequence[float]]] | None = None,
) -> int:
    """Set `follow_up_of` on stories in place; returns how many were annotated.

    One encode call covers current + archive titles. No-op (returns 0) when
    either list is empty or no embedding backend is available.
    """
    if not stories or not archive_stories:
        return 0
    if encode is None:
        encode = _load_encoder()
        if encode is None:
            return 0

    titles = [s.get("title", "") for s in stories]
    archive_titles = [s.get("title", "") for s in archive_stories]
    vecs = [_normalize(list(v)) for v in encode(titles + archive_titles)]
    cur, arc = vecs[:len(titles)], vecs[len(titles):]

    annotated = 0
    for i, story in enumerate(stories):
        best_sim = 0.0
        best_j = -1
        for j, av in enumerate(arc):
            sim = _cosine(cur[i], av)
            if sim > best_sim:
                best_sim, best_j = sim, j
        if best_j >= 0 and best_sim >= threshold:
            story["follow_up_of"] = archive_titles[best_j]
            annotated += 1
            log.info("thread (%.2f): %r ← %r", best_sim,
                     story.get("title", "")[:60], archive_titles[best_j][:60])
    return annotated


def annotate_from_archive(stories: list[dict],
                          digests_dir: str = DIGESTS_DIR,
                          lookback_days: int = LOOKBACK_DAYS) -> int:
    """Annotate `stories` against archive snapshots from the lookback window.

    Honors threads.enabled. Must run before the current run's snapshot is
    archived, or stories would thread against themselves.
    """
    if not ENABLED:
        return 0
    archive = collect_stories(digests_dir, lookback_days)
    if not archive:
        log.info("threading: no archive snapshots within %d days — skipping", lookback_days)
        return 0
    n = annotate_followups(stories, archive)
    log.info("threading: %d of %d stories marked as follow-ups", n, len(stories))
    return n
