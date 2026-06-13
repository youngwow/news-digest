"""ChunkMerger: classified chunks → dedup → training log → heuristics → classified.json."""

from __future__ import annotations

import glob
import json
import os
import re
from datetime import datetime, timezone

from ..config import Config
from ..jsonio import get_logger, save_json
from ..models import Story
from ..paths import ProjectPaths
from ..training.log import TrainingLog
from .dedup import Deduplicator
from .heuristics import ImportanceBooster, load_source_weights, load_url_published_map

log = get_logger("merge")


class ChunkMerger:
    def __init__(self, config: Config, paths: ProjectPaths, deduper: Deduplicator | None = None):
        self.config = config
        self.paths = paths
        self.chunks_dir = paths.data("chunks")
        self.deduper = deduper or Deduplicator(config.dedup)
        self.training_log = TrainingLog(
            paths.data("training", "dataset.jsonl"),
            paths.data("articles.json"),
            enabled=config.pipeline.training_log,
        )

    def _discover_chunk_indices(self) -> list[int]:
        indices = []
        for path in glob.glob(os.path.join(self.chunks_dir, "chunk_*.json")):
            m = re.fullmatch(r"chunk_(\d+)\.json", os.path.basename(path))
            if m:
                indices.append(int(m.group(1)))
        return sorted(indices)

    def _load_stories(self) -> list[Story]:
        stories: list[Story] = []
        for i in self._discover_chunk_indices():
            path = os.path.join(self.chunks_dir, f"chunk_{i}_classified.json")
            if not os.path.exists(path):
                log.warning("chunk_%d: SKIPPED (classified output missing — API failure)", i)
                continue
            with open(path, encoding="utf-8") as f:
                chunk = [Story.from_dict(s) for s in json.load(f).get("stories", [])]
            stories.extend(chunk)
            log.info("chunk_%d: %d stories", i, len(chunk))
        return stories

    def run(self) -> dict:
        stories = self._load_stories()
        raw_count = len(stories)
        log.info("Raw merge: %d stories", raw_count)

        deduped = self.deduper.dedup(stories)
        log.info("After dedup: %d stories (removed %d)", len(deduped), raw_count - len(deduped))

        # Distillation rows logged before heuristic boosts (raw LLM importance)
        self.training_log.append(deduped)

        booster = ImportanceBooster(
            self.config.heuristics,
            load_url_published_map(self.paths.data("articles.json")),
            load_source_weights(self.paths.sources_path),
        )
        boosted = booster.apply(deduped)
        log.info("Heuristics: boosted importance on %d/%d stories", boosted, len(deduped))

        out = {
            "classified_at": datetime.now(timezone.utc).isoformat(),
            "input_count": raw_count,
            "stories": [s.to_dict() for s in deduped],
        }
        os.makedirs(self.paths.data_dir, exist_ok=True)
        save_json(self.paths.data("classified.json"), out)
        log.info("Wrote %d stories → %s", len(deduped), self.paths.data("classified.json"))
        return out
