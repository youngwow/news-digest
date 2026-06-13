"""Pre-classification prep: raw_news.json → articles.json → chunk files; cleanup."""

from __future__ import annotations

import os
import shutil

from .config import Config
from .jsonio import get_logger, load_json, save_json
from .models import Article
from .paths import ProjectPaths

log = get_logger("chunking")


class Chunker:
    def __init__(self, config: Config, paths: ProjectPaths):
        self.config = config
        self.paths = paths
        self.chunks_dir = paths.data("chunks")

    def extract(self) -> list[dict]:
        """raw_news.json → flat articles.json."""
        data = load_json(self.paths.data("raw_news.json"))
        articles = [Article.from_dict(a).to_dict() for a in data.get("articles", [])]
        save_json(self.paths.data("articles.json"), {
            "collected_at": data.get("collected_at"),
            "total": len(articles),
            "articles": articles,
        })
        log.info("Extracted %d articles → %s", len(articles), self.paths.data("articles.json"))
        return articles

    def split(self) -> list[str]:
        """articles.json → chunk_N.json files of chunk_size articles each."""
        os.makedirs(self.chunks_dir, exist_ok=True)
        articles = load_json(self.paths.data("articles.json")).get("articles", [])
        size = self.config.pipeline.chunk_size
        chunks = [articles[i:i + size] for i in range(0, len(articles), size)]
        paths = []
        for idx, chunk in enumerate(chunks, 1):
            path = os.path.join(self.chunks_dir, f"chunk_{idx}.json")
            save_json(path, {"chunk_index": idx, "total_chunks": len(chunks), "articles": chunk})
            paths.append(path)
            log.info("Chunk %d/%d: %d articles → %s", idx, len(chunks), len(chunk), path)
        log.info("Split %d articles into %d chunks", len(articles), len(chunks))
        return paths

    def cleanup(self) -> None:
        if os.path.isdir(self.chunks_dir):
            shutil.rmtree(self.chunks_dir)
            log.info("Cleaned up %s", self.chunks_dir)


def validate_classified(paths: ProjectPaths, config: Config) -> dict:
    """Check classified.json structure; raise SystemExit listing every problem."""
    data = load_json(paths.data("classified.json"))
    stories = data.get("stories", [])
    problems = []
    for i, s in enumerate(stories):
        if not s.get("title"):
            problems.append(f"story[{i}]: missing title")
        if s.get("category") not in config.categories.emoji:
            problems.append(f"story[{i}]: invalid category: {s.get('category')!r}")
        if not isinstance(s.get("importance"), int):
            problems.append(
                f"story[{i}]: importance must be int, got {type(s.get('importance')).__name__}")
    if problems:
        for p in problems:
            log.error(p)
        raise SystemExit(f"classified.json: {len(problems)} validation error(s)")
    log.info("Validated: %d stories in classified.json", len(stories))
    return data
