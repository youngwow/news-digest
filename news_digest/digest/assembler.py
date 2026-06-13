"""DigestAssembler: classified.json → threading → digest.json + digest.md + archive."""

from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timezone

from ..config import Config
from ..jsonio import get_logger, load_json, save_json
from ..models import DigestDocument, Story
from ..paths import ProjectPaths
from ..sources.seen_urls import SeenUrlStore
from .archive import DigestArchive
from .builder import DigestBuilder
from .render import MarkdownRenderer
from .threads import StoryThreader

log = get_logger("assembler")


class DigestAssembler:
    def __init__(self, config: Config, paths: ProjectPaths,
                 threader: StoryThreader | None = None,
                 archive: DigestArchive | None = None):
        self.config = config
        self.paths = paths
        self.builder = DigestBuilder(config.categories)
        self.markdown = MarkdownRenderer(config.categories)
        self.threader = threader or StoryThreader(config.threads)
        self.archive = archive or DigestArchive(paths.data("digests"),
                                                config.archive.retention_days)
        self.seen = SeenUrlStore(paths.data("seen_urls.json"),
                                 config.dedup.cross_run_retention_days)

    def assemble(self) -> DigestDocument:
        data = load_json(self.paths.data("classified.json"))
        stories = [Story.from_dict(s) for s in data.get("stories", [])]
        if not stories:
            log.error("no stories to assemble")
            raise SystemExit(1)

        # Mark follow-ups before our own snapshot is archived
        self.threader.annotate_from_archive(stories, self.archive, self.config.dedup)

        digest = self.builder.build(stories, data.get("classified_at", ""))
        doc = DigestDocument(
            digest=digest,
            all_stories=stories,
            generated_at=datetime.now(timezone.utc).isoformat(),
            source_file=self.paths.data("raw_news.json"),
            total_raw=data.get("input_count", len(stories)),
            total_unique=len(stories),
        )

        os.makedirs(self.paths.data_dir, exist_ok=True)
        save_json(self.paths.data("digest.json"), doc.to_dict())
        log.info("Saved %s", self.paths.data("digest.json"))

        self.archive.write(doc)
        urls = [u for s in stories for u in s.urls]
        if urls:
            self.seen.mark(urls)
            log.info("Marked %d URLs as seen (retention: %d days)",
                     len(urls), self.config.dedup.cross_run_retention_days)

        md = self.markdown.render(doc)
        with open(self.paths.data("digest.md"), "w", encoding="utf-8") as f:
            f.write(md)
        log.info("Saved %s (%d chars)", self.paths.data("digest.md"), len(md))

        dist = Counter(s.category for s in stories)
        log.info("Category distribution: %s",
                 ", ".join(f"{c}={n}" for c, n in dist.most_common()))
        return doc
