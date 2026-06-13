"""Tests for WordOverlapDeduper on Story models."""

from news_digest.config import DedupConfig
from news_digest.digest.dedup import WordOverlapDeduper, title_words
from news_digest.models import Story


def _config() -> DedupConfig:
    return DedupConfig(overlap_threshold=0.5, shared_words_min=5, containment_min_len=15,
                       cross_run_enabled=True, cross_run_retention_days=1,
                       semantic_enabled=False, semantic_threshold=0.7,
                       semantic_model="m", semantic_model_static="m2")


def _stories() -> list[Story]:
    return [
        Story(title="Россия атаковала Украину массированным ударом дронов",
              category="политика", importance=8, sources=["src1"], urls=["http://a.com"]),
        Story(title="Россия атаковала Украину массированным ударом ракет и дронов",
              category="политика", importance=7, sources=["src2"], urls=["http://b.com"]),
        Story(title="Госдума разрешила Путину привлекать военных",
              category="политика", importance=6, sources=["src3"], urls=["http://c.com"]),
        Story(title="Госдума разрешила Путину привлекать военных для защиты россиян",
              category="политика", importance=6, sources=["src4"], urls=["http://d.com"]),
        Story(title="Запуск новой ракеты-носителя с космодрома Восточный",
              category="наука", importance=5, sources=["src5"], urls=["http://e.com"]),
    ]


def test_collapses_five_to_three_groups():
    assert len(WordOverlapDeduper(_config()).dedup(_stories())) == 3


def test_canonical_title_is_the_longer():
    result = WordOverlapDeduper(_config()).dedup(_stories())
    drone = next(s for s in result if "src1" in s.sources)
    assert drone.title == "Россия атаковала Украину массированным ударом ракет и дронов"


def test_importance_is_max_of_merged():
    result = WordOverlapDeduper(_config()).dedup(_stories())
    drone = next(s for s in result if "src1" in s.sources)
    assert drone.importance == 8


def test_title_words_drops_stopwords_and_short_tokens():
    words = title_words("Россия и США в новых переговорах")
    assert "россия" in words and "сша" in words and "переговорах" in words
    assert "и" not in words and "в" not in words
