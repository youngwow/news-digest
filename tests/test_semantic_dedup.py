"""Tests for SemanticDeduper with an injected fake encoder."""

from support import FakeEncoder

from news_digest.digest.dedup import SemanticDeduper
from news_digest.models import Story


def _story(title, source, importance=5):
    return Story(title=title, category="политика", importance=importance,
                 sources=[source], urls=[f"http://{source}.com/1"])


def test_paraphrases_above_threshold_merged():
    stories = [_story("музей сгорел после атаки дронов", "a", 7),
               _story("музей-панорама загорелся после удара беспилотника", "b", 8),
               _story("совсем другая новость", "c")]
    enc = FakeEncoder({
        "музей сгорел после атаки дронов": [1.0, 0.1, 0.0],
        "музей-панорама загорелся после удара беспилотника": [0.95, 0.2, 0.0],
        "совсем другая новость": [0.0, 0.0, 1.0],
    })
    result = SemanticDeduper(0.7, enc).dedup(stories)
    assert len(result) == 2
    merged = next(s for s in result if "a" in s.sources)
    assert sorted(merged.sources) == ["a", "b"]
    assert merged.importance == 8


def test_below_threshold_not_merged():
    stories = [_story("первая новость", "a"), _story("вторая новость", "b")]
    enc = FakeEncoder({"первая новость": [1.0, 0.0], "вторая новость": [0.5, 0.87]})
    assert len(SemanticDeduper(0.7, enc).dedup(stories)) == 2


def test_vectors_normalized_before_comparison():
    stories = [_story("новость", "a"), _story("та же новость", "b")]
    enc = FakeEncoder({"новость": [100.0, 0.0], "та же новость": [0.001, 0.0]})
    assert len(SemanticDeduper(0.99, enc).dedup(stories)) == 1


def test_single_story_passthrough():
    enc = FakeEncoder({"одна": [1.0]})
    assert len(SemanticDeduper(0.7, enc).dedup([_story("одна", "a")])) == 1
