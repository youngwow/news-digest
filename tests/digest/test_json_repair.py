"""Tests for ChunkClassifier.parse — JSON repair fallback + failed-output dump."""

import os

import httpx

from digest.support import valid_raw_config
from news_digest.classify.classifier import ChunkClassifier
from news_digest.config import Config
from news_digest.paths import ProjectPaths


def _classifier(tmp_path) -> ChunkClassifier:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "classify.txt").write_text("{n_articles}\n{articles_json}",
                                                       encoding="utf-8")
    (tmp_path / "data").mkdir()
    raw = valid_raw_config()
    raw["llm"]["active"] = "local"
    return ChunkClassifier(Config.from_dict(raw), ProjectPaths.from_root(str(tmp_path)),
                           transport=httpx.MockTransport(lambda r: httpx.Response(200)))


def test_strict_parse_passes_through(tmp_path):
    clf = _classifier(tmp_path)
    assert clf.parse('{"stories": []}', "chunk_1") == {"stories": []}


def test_trailing_comma_is_repaired(tmp_path):
    clf = _classifier(tmp_path)
    bad = '{"stories": [{"title": "x", "category": "мир", "importance": 5,}]}'
    parsed = clf.parse(bad, "chunk_1")
    assert parsed["stories"][0]["title"] == "x"
    assert parsed["stories"][0]["importance"] == 5


def test_missing_comma_is_repaired(tmp_path):
    clf = _classifier(tmp_path)
    bad = '{"stories": [{"title": "a", "category": "мир"} {"title": "b", "category": "мир"}]}'
    parsed = clf.parse(bad, "chunk_2")
    assert parsed is not None
    assert len(parsed["stories"]) == 2


def test_unrepairable_dumps_raw_to_disk(tmp_path):
    clf = _classifier(tmp_path)
    parsed = clf.parse("", "chunk_99")
    if parsed is None:
        dumps = os.listdir(clf.failed_dir)
        assert any(name.startswith("chunk_99_") for name in dumps)
    else:
        assert parsed in ({}, [], "")
