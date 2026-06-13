"""Tests for story_threads.py — follow-up annotation with an injected encoder."""

import json

import story_threads
from story_threads import annotate_followups, annotate_from_archive


def _story(title: str) -> dict:
    return {"title": title, "category": "политика", "importance": 5,
            "sources": ["src"], "urls": ["http://a.com/1"]}


def _fake_encoder(vectors_by_text: dict[str, list[float]]):
    return lambda texts: [vectors_by_text[t] for t in texts]


def test_followup_above_threshold_is_annotated():
    stories = [_story("Дерипаска потребовал взыскать 1 млрд")]
    archive = [_story("Дерипаска подал иск к расследователям"),
               _story("совсем другая старая новость")]
    encode = _fake_encoder({
        "Дерипаска потребовал взыскать 1 млрд": [1.0, 0.2],
        "Дерипаска подал иск к расследователям": [1.0, 0.3],   # cos ≈ 0.99
        "совсем другая старая новость": [0.0, 1.0],
    })
    n = annotate_followups(stories, archive, threshold=0.6, encode=encode)
    assert n == 1
    assert stories[0]["follow_up_of"] == "Дерипаска подал иск к расследователям"


def test_best_archive_match_wins():
    stories = [_story("новость")]
    archive = [_story("слабое совпадение"), _story("сильное совпадение")]
    encode = _fake_encoder({
        "новость": [1.0, 0.0],
        "слабое совпадение": [0.8, 0.6],     # cos = 0.8
        "сильное совпадение": [0.99, 0.14],  # cos ≈ 0.99
    })
    annotate_followups(stories, archive, threshold=0.6, encode=encode)
    assert stories[0]["follow_up_of"] == "сильное совпадение"


def test_below_threshold_untouched():
    stories = [_story("свежая новость")]
    archive = [_story("несвязанная старая")]
    encode = _fake_encoder({
        "свежая новость": [1.0, 0.0],
        "несвязанная старая": [0.3, 0.95],
    })
    assert annotate_followups(stories, archive, threshold=0.6, encode=encode) == 0
    assert "follow_up_of" not in stories[0]


def test_empty_archive_is_noop():
    stories = [_story("новость")]
    assert annotate_followups(stories, [], threshold=0.6, encode=None) == 0


def test_no_backend_is_noop(monkeypatch):
    monkeypatch.setattr(story_threads, "_load_encoder", lambda: None)
    stories = [_story("новость")]
    assert annotate_followups(stories, [_story("старая")], threshold=0.6) == 0


# ── annotate_from_archive ───────────────────────────────────────────

def _write_snapshot(dirpath, ts: str, stories: list[dict]) -> None:
    (dirpath / f"digest-{ts}.json").write_text(
        json.dumps({"all_stories": stories}, ensure_ascii=False), encoding="utf-8")


def test_disabled_flag_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(story_threads, "ENABLED", False)
    _write_snapshot(tmp_path, "2099-01-01T00-00", [_story("старая")])
    assert annotate_from_archive([_story("новая")], str(tmp_path)) == 0


def test_missing_archive_dir_is_noop(monkeypatch):
    monkeypatch.setattr(story_threads, "ENABLED", True)
    assert annotate_from_archive([_story("новая")], "/nonexistent/dir") == 0
