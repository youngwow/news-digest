"""WeeklyRollup: aggregate recent archive snapshots into one rollup digest."""

from __future__ import annotations

from datetime import datetime, timezone

from ..config import Config
from ..jsonio import get_logger
from ..models import DigestDocument
from ..paths import ProjectPaths
from .archive import DigestArchive
from .builder import DigestBuilder
from .dedup import WordOverlapDeduper

log = get_logger("rollup")


class WeeklyRollup:
    def __init__(self, config: Config, paths: ProjectPaths,
                 archive: DigestArchive | None = None,
                 deduper: WordOverlapDeduper | None = None):
        self.config = config
        self.archive = archive or DigestArchive(paths.data("digests"),
                                                config.archive.retention_days)
        self.deduper = deduper or WordOverlapDeduper(config.dedup)
        self.builder = DigestBuilder(config.categories)

    def build(self, days: int = 7, now: datetime | None = None) -> DigestDocument | None:
        """Merge snapshots within the window into a rollup, or None if none found."""
        stories = self.archive.recent_stories(days, now)
        if not stories:
            return None
        deduped = self.deduper.dedup(stories)
        log.info("Rollup: %d stories → %d after dedup (last %d days)",
                 len(stories), len(deduped), days)
        digest = self.builder.build(deduped, datetime.now(timezone.utc).isoformat())
        return DigestDocument(
            digest=digest,
            all_stories=deduped,
            generated_at=datetime.now(timezone.utc).isoformat(),
            total_raw=len(stories),
            total_unique=len(deduped),
        )
