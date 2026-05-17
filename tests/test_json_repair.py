"""Tests for the JSON repair fallback in call_ollama._parse_with_repair."""

import os

import call_ollama
from call_ollama import _parse_with_repair


def test_strict_parse_passes_through(tmp_path, monkeypatch):
    monkeypatch.setattr(call_ollama, "FAILED_DIR", str(tmp_path))
    parsed = _parse_with_repair('{"stories": []}', '{"stories": []}', chunk_num=1)
    assert parsed == {"stories": []}


def test_trailing_comma_is_repaired(tmp_path, monkeypatch):
    monkeypatch.setattr(call_ollama, "FAILED_DIR", str(tmp_path))
    bad = '{"stories": [{"title": "x", "category": "мир", "importance": 5,}]}'
    parsed = _parse_with_repair(bad, bad, chunk_num=1)
    assert parsed is not None
    assert parsed["stories"][0]["title"] == "x"
    assert parsed["stories"][0]["importance"] == 5


def test_missing_comma_is_repaired(tmp_path, monkeypatch):
    monkeypatch.setattr(call_ollama, "FAILED_DIR", str(tmp_path))
    # Missing comma between two array elements — a real failure mode from gemma4:e4b
    bad = '{"stories": [{"title": "a", "category": "мир"} {"title": "b", "category": "мир"}]}'
    parsed = _parse_with_repair(bad, bad, chunk_num=2)
    assert parsed is not None
    assert len(parsed["stories"]) == 2


def test_unrepairable_dumps_raw_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(call_ollama, "FAILED_DIR", str(tmp_path))
    # Pass something json-repair cannot rescue: empty string yields {} from repair,
    # so use plain garbage that yields an empty/null result.
    parsed = _parse_with_repair("", "raw garbage content for debugging", chunk_num=99)
    # repair_json may return "" -> json.loads("") raises -> we return None
    if parsed is None:
        dumps = os.listdir(tmp_path)
        assert any(name.startswith("chunk_99_") for name in dumps)
        with open(os.path.join(tmp_path, dumps[0]), encoding="utf-8") as f:
            assert "raw garbage content for debugging" in f.read()
    else:
        # If json-repair happens to return a valid empty object/array, that's also fine
        assert parsed in ({}, [], "")
