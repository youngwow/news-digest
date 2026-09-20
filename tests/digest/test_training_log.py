"""Tests for TrainingLog — the distillation dataset appender."""

import json

from news_digest.models import Story
from news_digest.training.log import TrainingLog

STORY = Story(title="Каноничный заголовок сюжета", category="политика", importance=8,
              sources=["Meduza", "SOTA"],
              urls=["http://a.com/1", "http://b.com/2", "http://gone.com/3"])

ARTICLES = {
    "http://a.com/1": {"url": "http://a.com/1", "title": "Заголовок А",
                       "summary": "Краткое А", "source": "Meduza",
                       "published": "2026-06-10T10:00:00+00:00"},
    "http://b.com/2": {"url": "http://b.com/2", "title": "Заголовок Б",
                       "summary": "Краткое Б", "source": "SOTA",
                       "published": "2026-06-10T11:00:00+00:00"},
}


def test_one_row_per_resolvable_url():
    rows = TrainingLog.build_rows([STORY], ARTICLES)
    assert len(rows) == 2  # http://gone.com/3 unresolved → skipped


def test_row_joins_article_features_with_story_label():
    row = TrainingLog.build_rows([STORY], ARTICLES)[0]
    assert row["title"] == "Заголовок А"          # article's own text
    assert row["summary"] == "Краткое А"
    assert row["category"] == "политика"           # label from the story
    assert row["importance"] == 8
    assert row["story_title"] == "Каноничный заголовок сюжета"
    assert row["logged_at"]


def _articles_file(tmp_path) -> str:
    path = tmp_path / "articles.json"
    path.write_text(json.dumps({"articles": list(ARTICLES.values())}, ensure_ascii=False),
                    encoding="utf-8")
    return str(path)


def test_append_writes_jsonl(tmp_path):
    dataset = tmp_path / "training" / "dataset.jsonl"
    log = TrainingLog(str(dataset), _articles_file(tmp_path), enabled=True)
    assert log.append([STORY]) == 2
    lines = dataset.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["url"] == "http://a.com/1"


def test_append_accumulates(tmp_path):
    dataset = tmp_path / "training" / "dataset.jsonl"
    log = TrainingLog(str(dataset), _articles_file(tmp_path), enabled=True)
    log.append([STORY])
    log.append([STORY])
    assert len(dataset.read_text(encoding="utf-8").strip().split("\n")) == 4


def test_disabled_is_noop(tmp_path):
    dataset = tmp_path / "training" / "dataset.jsonl"
    log = TrainingLog(str(dataset), _articles_file(tmp_path), enabled=False)
    assert log.append([STORY]) == 0
    assert not dataset.exists()


def test_missing_articles_file_is_noop(tmp_path):
    dataset = tmp_path / "training" / "dataset.jsonl"
    log = TrainingLog(str(dataset), str(tmp_path / "nope.json"), enabled=True)
    assert log.append([STORY]) == 0
    assert not dataset.exists()
