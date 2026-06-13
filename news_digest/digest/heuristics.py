"""Importance heuristics: multi-source, source-weight, and recency boosts."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from ..config import HeuristicsConfig
from ..jsonio import load_json
from ..models import Story


def load_url_published_map(articles_path: str) -> dict[str, str]:
    """Build {url: published-ISO} from articles.json (best-effort, empty on miss)."""
    if not os.path.exists(articles_path):
        return {}
    try:
        data = load_json(articles_path)
    except (ValueError, OSError):
        return {}
    return {a["url"]: a["published"] for a in data.get("articles", [])
            if a.get("url") and a.get("published")}


def load_source_weights(sources_path: str) -> dict[str, float]:
    """Read sources.json → {source_name: weight} (default 1.0)."""
    if not os.path.exists(sources_path):
        return {}
    try:
        data = load_json(sources_path)
    except (ValueError, OSError):
        return {}
    return {s["name"]: float(s.get("weight", 1.0)) for s in data.get("sources", [])}


class ImportanceBooster:
    """Adds small, capped bumps to the LLM-assigned importance (≤ 10)."""

    def __init__(self, config: HeuristicsConfig,
                 url_published: dict[str, str] | None = None,
                 source_weights: dict[str, float] | None = None):
        self.config = config
        self.url_published = url_published or {}
        self.source_weights = source_weights or {}

    def boost(self, story: Story, now: datetime | None = None) -> int:
        importance = int(story.importance)

        unique_sources = len(set(story.sources))
        if unique_sources > 1:
            importance += min(unique_sources - 1, self.config.multi_source_max_boost)

        if self.source_weights:
            weights = [self.source_weights.get(src, 1.0) for src in story.sources]
            if weights:
                extra = int(max(weights) - 1.0)
                if extra > 0:
                    importance += min(extra, self.config.source_weight_max_boost)

        if self.config.recency_boost and self.url_published:
            ref_now = now or datetime.now(timezone.utc)
            cutoff = ref_now - timedelta(hours=self.config.recency_window_hours)
            for url in story.urls:
                pub_str = self.url_published.get(url)
                if not pub_str:
                    continue
                try:
                    pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                except (ValueError, TypeError, AttributeError):
                    continue
                if pub_dt >= cutoff:
                    importance += self.config.recency_boost
                    break

        return min(importance, 10)

    def apply(self, stories: list[Story], now: datetime | None = None) -> int:
        """Boost every story in place; return how many changed."""
        boosted = 0
        for s in stories:
            new = self.boost(s, now)
            if new != s.importance:
                boosted += 1
            s.importance = new
        return boosted
