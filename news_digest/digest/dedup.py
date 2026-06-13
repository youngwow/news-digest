"""Cross-chunk deduplication: word-overlap then optional embedding pass."""

from __future__ import annotations

from ..config import DedupConfig
from ..jsonio import get_logger
from ..models import Story
from .encoders import Encoder, cosine, load_encoder, normalize

log = get_logger("dedup")

# Russian stop words (plus reporting verbs) ignored when comparing titles
_STOP_WORDS = frozenset({
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то", "все",
    "она", "так", "но", "его", "по", "из", "у", "же", "за", "от", "о", "бы", "для",
    "это", "или", "до", "мы", "их", "был", "еще", "к", "когда", "да", "вы", "при",
    "без", "под", "нет", "ли", "там", "где", "уже", "если", "быть", "себя", "этот",
    "будет", "после", "может", "теперь", "сейчас", "через", "пока", "них", "здесь",
    "также", "только", "сообщил", "заявил", "рассказал", "пишет", "написал",
    "новый", "стало", "известно", "очень",
})


def title_words(title: str) -> set[str]:
    """Significant words of a title, lowercased, stop words and short tokens dropped."""
    t = title.lower()
    for ch in ".,!?;:—«»\"'()[]{}…":
        t = t.replace(ch, " ")
    return {w for w in t.split() if len(w) > 2 and w not in _STOP_WORDS}


class WordOverlapDeduper:
    """Merges stories whose titles share enough significant words or contain one another."""

    def __init__(self, config: DedupConfig):
        self.overlap_threshold = config.overlap_threshold
        self.shared_words_min = config.shared_words_min
        self.containment_min_len = config.containment_min_len

    def dedup(self, stories: list[Story]) -> list[Story]:
        n = len(stories)
        word_sets = [title_words(s.title) for s in stories]
        used = [False] * n
        merged: list[Story] = []
        for i in range(n):
            if used[i]:
                continue
            story = stories[i].copy()
            wi, ti = word_sets[i], stories[i].title.lower().rstrip(".")
            for j in range(i + 1, n):
                if used[j] or not wi or not word_sets[j]:
                    continue
                wj, tj = word_sets[j], stories[j].title.lower().rstrip(".")
                shared, total = wi & wj, wi | wj
                overlap = len(shared) / len(total) if total else 0
                if (overlap > self.overlap_threshold or len(shared) >= self.shared_words_min
                        or (len(ti) > self.containment_min_len and ti in tj)
                        or (len(tj) > self.containment_min_len and tj in ti)):
                    story.merge(stories[j])
                    used[j] = True
            merged.append(story)
        return merged


class SemanticDeduper:
    """Merges stories whose title embeddings reach cosine >= threshold.

    Catches Russian paraphrases that exact word forms miss.
    """

    def __init__(self, threshold: float, encoder: Encoder):
        self.threshold = threshold
        self.encoder = encoder

    def dedup(self, stories: list[Story]) -> list[Story]:
        if len(stories) < 2:
            return stories
        vecs = [normalize(list(v)) for v in self.encoder.encode([s.title for s in stories])]
        n = len(stories)
        used = [False] * n
        merged: list[Story] = []
        for i in range(n):
            if used[i]:
                continue
            story = stories[i].copy()
            for j in range(i + 1, n):
                if used[j]:
                    continue
                sim = cosine(vecs[i], vecs[j])
                if sim >= self.threshold:
                    log.info("semantic merge (%.2f): %r + %r",
                             sim, story.title[:60], stories[j].title[:60])
                    story.merge(stories[j])
                    used[j] = True
            merged.append(story)
        return merged


class Deduplicator:
    """Facade: word-overlap pass, then the embedding pass when enabled/available."""

    def __init__(self, config: DedupConfig, encoder: Encoder | None = None):
        self.config = config
        self.word = WordOverlapDeduper(config)
        self._encoder = encoder
        self._encoder_resolved = encoder is not None

    def _get_encoder(self) -> Encoder | None:
        if not self._encoder_resolved:
            self._encoder = load_encoder(self.config)
            self._encoder_resolved = True
        return self._encoder

    def dedup(self, stories: list[Story]) -> list[Story]:
        before = len(stories)
        stories = self.word.dedup(stories)
        log.info("word-overlap dedup: %d → %d", before, len(stories))
        if self.config.semantic_enabled:
            encoder = self._get_encoder()
            if encoder is not None:
                before = len(stories)
                stories = SemanticDeduper(self.config.semantic_threshold, encoder).dedup(stories)
                log.info("semantic dedup: %d → %d (removed %d paraphrases)",
                         before, len(stories), before - len(stories))
        return stories
