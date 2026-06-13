"""TrainingLog: append (article features → LLM label) rows for classifier distillation.

Each merge run appends one row per (article, story) pair to dataset.jsonl: the
article's own text joined with the story's LLM-assigned category/importance.
Rows are written before heuristic boosts, so importance is the raw LLM label.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from ..jsonio import get_logger, load_json
from ..models import Story

log = get_logger("training_log")


class TrainingLog:
    def __init__(self, dataset_path: str, articles_path: str, enabled: bool = True):
        self.dataset_path = dataset_path
        self.articles_path = articles_path
        self.enabled = enabled

    @staticmethod
    def build_rows(stories: list[Story], articles_by_url: dict[str, dict]) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        rows: list[dict] = []
        for story in stories:
            for url in story.urls:
                article = articles_by_url.get(url)
                if article is None:
                    continue
                rows.append({
                    "url": url,
                    "title": article.get("title", ""),
                    "summary": article.get("summary", ""),
                    "source": article.get("source", ""),
                    "published": article.get("published"),
                    "category": story.category,
                    "importance": story.importance,
                    "story_title": story.title,
                    "logged_at": now,
                })
        return rows

    def append(self, stories: list[Story]) -> int:
        """Append distillation rows; return the count. Best-effort: never raises."""
        if not self.enabled:
            return 0
        try:
            articles = load_json(self.articles_path).get("articles", [])
        except (OSError, ValueError) as e:
            log.warning("training log: cannot read %s (%s) — skipping", self.articles_path, e)
            return 0
        articles_by_url = {a["url"]: a for a in articles if a.get("url")}
        rows = self.build_rows(stories, articles_by_url)
        if not rows:
            return 0
        try:
            os.makedirs(os.path.dirname(self.dataset_path), exist_ok=True)
            with open(self.dataset_path, "a", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("training log: cannot write %s (%s) — skipping", self.dataset_path, e)
            return 0
        log.info("training log: appended %d rows → %s", len(rows), self.dataset_path)
        return len(rows)
