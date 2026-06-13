"""Tests for training_log.py — the distillation dataset appender."""

import json

import training_log
from training_log import append_training_rows, build_rows

STORY = {
    "title": "Каноничный заголовок сюжета",
    "category": "политика",
    "importance": 8,
    "sources": ["Meduza", "SOTA"],
    "urls": ["http://a.com/1", "http://b.com/2", "http://gone.com/3"],
}

ARTICLES = {
    "http://a.com/1": {"url": "http://a.com/1", "title": "Заголовок А",
                       "summary": "Краткое содержание А", "source": "Meduza",
                       "published": "2026-06-10T10:00:00+00:00"},
    "http://b.com/2": {"url": "http://b.com/2", "title": "Заголовок Б",
                       "summary": "Краткое содержание Б", "source": "SOTA",
                       "published": "2026-06-10T11:00:00+00:00"},
}


# ── build_rows ──────────────────────────────────────────────────────

def test_one_row_per_resolvable_url():
    rows = build_rows([STORY], ARTICLES)
    assert len(rows) == 2  # http://gone.com/3 has no scraped article → skipped


def test_row_joins_article_features_with_story_label():
    row = build_rows([STORY], ARTICLES)[0]
    assert row["title"] == "Заголовок А"          # article's own text, not the story's
    assert row["summary"] == "Краткое содержание А"
    assert row["category"] == "политика"           # label comes from the story
    assert row["importance"] == 8
    assert row["story_title"] == "Каноничный заголовок сюжета"
    assert row["logged_at"]


# ── append_training_rows ────────────────────────────────────────────

def _articles_file(tmp_path) -> str:
    path = tmp_path / "articles.json"
    path.write_text(json.dumps({"articles": list(ARTICLES.values())}, ensure_ascii=False),
                    encoding="utf-8")
    return str(path)


def test_append_writes_jsonl(tmp_path):
    dataset = tmp_path / "training" / "dataset.jsonl"
    n = append_training_rows([STORY], _articles_file(tmp_path), str(dataset))
    assert n == 2
    lines = dataset.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["url"] == "http://a.com/1"


def test_append_accumulates_across_runs(tmp_path):
    dataset = tmp_path / "training" / "dataset.jsonl"
    articles = _articles_file(tmp_path)
    append_training_rows([STORY], articles, str(dataset))
    append_training_rows([STORY], articles, str(dataset))
    assert len(dataset.read_text(encoding="utf-8").strip().split("\n")) == 4


def test_disabled_flag_is_noop(tmp_path, monkeypatch):
    monkeypatch.setitem(training_log.CONFIG["pipeline"], "training_log", False)
    dataset = tmp_path / "training" / "dataset.jsonl"
    n = append_training_rows([STORY], _articles_file(tmp_path), str(dataset))
    assert n == 0
    assert not dataset.exists()


def test_missing_articles_file_is_noop(tmp_path):
    dataset = tmp_path / "training" / "dataset.jsonl"
    n = append_training_rows([STORY], str(tmp_path / "nope.json"), str(dataset))
    assert n == 0
    assert not dataset.exists()
