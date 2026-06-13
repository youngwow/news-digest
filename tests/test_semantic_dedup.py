"""Tests for merge_chunks.dedup_semantic — embedding pass with an injected encoder."""

import merge_chunks
from merge_chunks import dedup_semantic


def _story(title: str, source: str, importance: int = 5) -> dict:
    return {"title": title, "category": "политика", "importance": importance,
            "sources": [source], "urls": [f"http://{source}.com/1"]}


def _fake_encoder(vectors_by_text: dict[str, list[float]]):
    """Encoder stub: maps each title to a fixed (not necessarily unit) vector."""
    return lambda texts: [vectors_by_text[t] for t in texts]


def test_paraphrases_above_threshold_are_merged():
    stories = [_story("музей сгорел после атаки дронов", "a", importance=7),
               _story("музей-панорама загорелся после удара беспилотника", "b", importance=8),
               _story("совсем другая новость", "c")]
    encode = _fake_encoder({
        "музей сгорел после атаки дронов": [1.0, 0.1, 0.0],
        "музей-панорама загорелся после удара беспилотника": [0.95, 0.2, 0.0],
        "совсем другая новость": [0.0, 0.0, 1.0],
    })
    result = dedup_semantic(stories, threshold=0.7, encode=encode)
    assert len(result) == 2
    merged = next(s for s in result if "a" in s["sources"])
    assert sorted(merged["sources"]) == ["a", "b"]
    assert merged["importance"] == 8  # max of the merged pair
    assert len(merged["urls"]) == 2


def test_below_threshold_not_merged():
    stories = [_story("первая новость", "a"), _story("вторая новость", "b")]
    encode = _fake_encoder({
        "первая новость": [1.0, 0.0],
        "вторая новость": [0.5, 0.87],  # cosine ≈ 0.5
    })
    result = dedup_semantic(stories, threshold=0.7, encode=encode)
    assert len(result) == 2


def test_vectors_are_normalized_before_comparison():
    """Magnitude must not matter — only direction."""
    stories = [_story("новость", "a"), _story("та же новость", "b")]
    encode = _fake_encoder({
        "новость": [100.0, 0.0],
        "та же новость": [0.001, 0.0],  # same direction, wildly different norms
    })
    result = dedup_semantic(stories, threshold=0.99, encode=encode)
    assert len(result) == 1


def test_single_story_passthrough():
    stories = [_story("одна новость", "a")]
    assert dedup_semantic(stories, threshold=0.7, encode=None) == stories


def test_no_backend_returns_input_unchanged(monkeypatch):
    monkeypatch.setattr(merge_chunks, "_load_encoder", lambda: None)
    stories = [_story("первая", "a"), _story("вторая", "b")]
    assert dedup_semantic(stories, threshold=0.7) == stories


def test_zero_vector_title_never_merges():
    stories = [_story("", "a"), _story("новость", "b")]
    encode = _fake_encoder({"": [0.0, 0.0], "новость": [1.0, 0.0]})
    result = dedup_semantic(stories, threshold=0.1, encode=encode)
    assert len(result) == 2
