"""Integration test for ChunkMerger — chunks → dedup → heuristics → classified.json."""

import json

from support import valid_raw_config

from news_digest.config import Config
from news_digest.digest.merge import ChunkMerger
from news_digest.paths import ProjectPaths


def _setup(tmp_path):
    (tmp_path / "data" / "chunks").mkdir(parents=True)
    (tmp_path / "data" / "training").mkdir(parents=True)
    # two near-duplicate stories across one chunk → word-overlap merges them
    chunk = {"chunk_size": 2, "stories": [
        {"title": "Россия атаковала Украину массированным ударом дронов",
         "category": "политика", "importance": 8, "sources": ["a"], "urls": ["http://a/1"]},
        {"title": "Россия атаковала Украину массированным ударом ракет и дронов",
         "category": "политика", "importance": 7, "sources": ["b"], "urls": ["http://b/2"]},
        {"title": "Запуск ракеты с космодрома Восточный",
         "category": "наука", "importance": 5, "sources": ["c"], "urls": ["http://c/3"]},
    ]}
    # the input chunk file is what discovery globs; the _classified file holds output
    (tmp_path / "data" / "chunks" / "chunk_1.json").write_text(
        json.dumps({"articles": []}), encoding="utf-8")
    (tmp_path / "data" / "chunks" / "chunk_1_classified.json").write_text(
        json.dumps(chunk, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "data" / "articles.json").write_text(json.dumps({"articles": [
        {"url": "http://a/1", "title": "A", "summary": "s", "source": "a", "published": None},
        {"url": "http://b/2", "title": "B", "summary": "s", "source": "b", "published": None},
        {"url": "http://c/3", "title": "C", "summary": "s", "source": "c", "published": None},
    ]}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "sources.json").write_text(json.dumps({"sources": []}), encoding="utf-8")
    cfg = Config.from_dict(valid_raw_config())  # semantic_enabled False
    return ChunkMerger(cfg, ProjectPaths.from_root(str(tmp_path)))


def test_merge_dedups_and_writes_classified(tmp_path):
    merger = _setup(tmp_path)
    out = merger.run()
    assert out["input_count"] == 3
    assert len(out["stories"]) == 2  # the two drone stories merged
    written = json.loads((tmp_path / "data" / "classified.json").read_text())
    assert written["input_count"] == 3


def test_merge_writes_training_rows(tmp_path):
    merger = _setup(tmp_path)
    merger.run()
    dataset = tmp_path / "data" / "training" / "dataset.jsonl"
    assert dataset.exists()
    rows = [json.loads(line) for line in dataset.read_text().strip().split("\n")]
    assert len(rows) == 3  # one per resolvable URL
    assert {r["category"] for r in rows} == {"политика", "наука"}
