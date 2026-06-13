#!/usr/bin/env python3
"""
Training-data logger: distillation dataset for a future local classifier.

Every pipeline run, merge_chunks.py appends one row per (article, story-label)
pair to data/training/dataset.jsonl — the article's own text features joined
with the LLM-assigned category/importance of the story it was merged into.
The LLM acts as the teacher; once enough rows accumulate, a small local model
(e.g. rubert-tiny2) can be fine-tuned to replicate the labels.

Rows are logged *before* heuristic importance boosts, so `importance` is the
raw LLM label, not the runtime-adjusted score. Toggle via
config.pipeline.training_log. The dataset lives outside `make clean`'s globs
and survives cleanups; consumers should dedup rows by `url`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from utils import CONFIG, DATA_DIR, get_logger

ARTICLES_JSON = os.path.join(DATA_DIR, "articles.json")
TRAINING_DIR = os.path.join(DATA_DIR, "training")
DATASET_PATH = os.path.join(TRAINING_DIR, "dataset.jsonl")

log = get_logger("training_log")


def build_rows(stories: list[dict], articles_by_url: dict[str, dict]) -> list[dict]:
    """One row per story URL that resolves to a scraped article."""
    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for story in stories:
        for url in story.get("urls", []):
            article = articles_by_url.get(url)
            if article is None:
                continue
            rows.append({
                "url": url,
                "title": article.get("title", ""),
                "summary": article.get("summary", ""),
                "source": article.get("source", ""),
                "published": article.get("published"),
                "category": story.get("category", ""),
                "importance": story.get("importance"),
                "story_title": story.get("title", ""),
                "logged_at": now,
            })
    return rows


def append_training_rows(stories: list[dict],
                         articles_path: str = ARTICLES_JSON,
                         dataset_path: str = DATASET_PATH) -> int:
    """Append distillation rows for `stories`; returns the row count.

    Best-effort side artifact: any failure logs a warning and returns 0 —
    it must never break the merge step.
    """
    if not CONFIG["pipeline"]["training_log"]:
        return 0

    try:
        with open(articles_path, encoding="utf-8") as f:
            articles = json.load(f).get("articles", [])
    except (OSError, json.JSONDecodeError) as e:
        log.warning("training log: cannot read %s (%s) — skipping", articles_path, e)
        return 0

    articles_by_url = {a["url"]: a for a in articles if a.get("url")}
    rows = build_rows(stories, articles_by_url)
    if not rows:
        return 0

    try:
        os.makedirs(os.path.dirname(dataset_path), exist_ok=True)
        with open(dataset_path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("training log: cannot write %s (%s) — skipping", dataset_path, e)
        return 0

    log.info("training log: appended %d rows → %s", len(rows), dataset_path)
    return len(rows)
