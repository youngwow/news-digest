"""StoryThreader: mark stories that develop a recently-delivered story (🔄).

Cross-run dedup is URL-exact, so a new article on yesterday's storyline looks
brand-new. This compares current titles against recent archive titles via the
same embedding backend as semantic dedup; a best match >= threshold sets
`follow_up_of`, and renderers prefix the title with 🔄.
"""

from __future__ import annotations

from ..config import ThreadsConfig
from ..jsonio import get_logger
from ..models import Story
from .archive import DigestArchive
from .encoders import Encoder, cosine, load_encoder, normalize

log = get_logger("story_threads")


class StoryThreader:
    def __init__(self, config: ThreadsConfig, encoder: Encoder | None = None):
        self.config = config
        self._encoder = encoder
        self._encoder_resolved = encoder is not None

    def _get_encoder(self, dedup_config) -> Encoder | None:
        if not self._encoder_resolved:
            self._encoder = load_encoder(dedup_config)
            self._encoder_resolved = True
        return self._encoder

    def annotate(self, stories: list[Story], archive_stories: list[Story]) -> int:
        """Set `follow_up_of` on stories in place; return how many were annotated."""
        if not stories or not archive_stories or self._encoder is None:
            return 0
        titles = [s.title for s in stories]
        archive_titles = [s.title for s in archive_stories]
        vecs = [normalize(list(v)) for v in self._encoder.encode(titles + archive_titles)]
        cur, arc = vecs[:len(titles)], vecs[len(titles):]
        annotated = 0
        for i, story in enumerate(stories):
            best_sim, best_j = 0.0, -1
            for j, av in enumerate(arc):
                sim = cosine(cur[i], av)
                if sim > best_sim:
                    best_sim, best_j = sim, j
            if best_j >= 0 and best_sim >= self.config.similarity_threshold:
                story.follow_up_of = archive_titles[best_j]
                annotated += 1
                log.info("thread (%.2f): %r ← %r",
                         best_sim, story.title[:60], archive_titles[best_j][:60])
        return annotated

    def annotate_from_archive(self, stories: list[Story], archive: DigestArchive,
                              dedup_config) -> int:
        """Pull recent archive stories and annotate. Honors threads.enabled.

        Must run before the current run's snapshot is archived.
        """
        if not self.config.enabled:
            return 0
        recent = archive.recent_stories(self.config.lookback_days)
        if not recent:
            log.info("threading: no archive snapshots within %d days — skipping",
                     self.config.lookback_days)
            return 0
        if self._get_encoder(dedup_config) is None:
            return 0
        n = self.annotate(stories, recent)
        log.info("threading: %d of %d stories marked as follow-ups", n, len(stories))
        return n
