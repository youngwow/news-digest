"""Tests for utils.py: extract_json, load_api_key, _validate_config."""


import pytest

import utils
from utils import _validate_config, extract_json, load_api_key

# ── extract_json ────────────────────────────────────────────────

def test_extract_json_fenced_object():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_json_fenced_array():
    assert extract_json('```json\n[1, 2, 3]\n```') == '[1, 2, 3]'


def test_extract_json_unfenced_object():
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_unfenced_array():
    assert extract_json('[1, 2, 3]') == '[1, 2, 3]'


def test_extract_json_with_leading_chatter():
    text = 'Here is the result:\n{"stories": []}\nDone.'
    assert extract_json(text) == '{"stories": []}'


def test_extract_json_picks_outermost_pair():
    text = 'noise {"a": {"b": 1}} more noise'
    assert extract_json(text) == '{"a": {"b": 1}}'


def test_extract_json_empty_input():
    assert extract_json("") == ""


def test_extract_json_no_json_returns_empty():
    assert extract_json("no json here, just plain text") == ""


def test_extract_json_unterminated_returns_empty():
    assert extract_json("[1, 2, 3, incomplete") == ""


def test_extract_json_picks_whichever_opens_first():
    assert extract_json('{"a": 1} then [2]') == '{"a": 1}'
    assert extract_json('[1, 2] then {"b": 3}') == '[1, 2]'


# ── load_api_key ────────────────────────────────────────────────

def test_load_api_key_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_API_KEY", "from_env_key")
    monkeypatch.setattr(utils, "PROJECT_ROOT", str(tmp_path))
    (tmp_path / ".env").write_text('OLLAMA_API_KEY="from_file_key"\n')
    assert load_api_key() == "from_env_key"


def test_load_api_key_falls_back_to_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setattr(utils, "PROJECT_ROOT", str(tmp_path))
    (tmp_path / ".env").write_text('OLLAMA_API_KEY="from_file_key"\n')
    assert load_api_key() == "from_file_key"


def test_load_api_key_strips_quotes(monkeypatch, tmp_path):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setattr(utils, "PROJECT_ROOT", str(tmp_path))
    (tmp_path / ".env").write_text("OLLAMA_API_KEY='bare_key'\n")
    assert load_api_key() == "bare_key"


def test_load_api_key_returns_empty_when_nothing_set(monkeypatch, tmp_path):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setattr(utils, "PROJECT_ROOT", str(tmp_path))
    assert load_api_key() == ""


# ── _validate_config ────────────────────────────────────────────

def _good_config() -> dict:
    """Minimal valid config covering all required keys."""
    return {
        "llm": {
            "active": "cloud",
            "classify_concurrency": 4,
            "cache_retention_days": 7,
            "failure_tolerance": 0.34,
            "bad_json_retry": True,
            "providers": [
                {
                    "name": "cloud", "base_url": "https://x", "model": "m",
                    "temperature": 0.3, "max_tokens": 1024,
                    "timeout": 30, "max_retries": 3,
                    "api_key_env": "OLLAMA_API_KEY",
                },
            ],
        },
        "scraper": {
            "date_window_hours": 8, "request_timeout": 20,
            "max_redirects": 5, "user_agent": "x",
            "fetch_bodies": False, "body_max_chars": 3000,
            "body_timeout": 10, "body_concurrency": 8,
            "body_cache_retention_days": 14,
        },
        "pipeline": {"chunk_size": 15},
        "telegram": {"message_limit": 4096},
        "health": {
            "min_sources_ok": 3, "expected_sources": 10,
            "default_max_age_minutes": 360,
        },
        "dedup": {
            "overlap_threshold": 0.3, "shared_words_min": 3,
            "containment_min_len": 15,
            "cross_run_enabled": True, "cross_run_retention_days": 3,
        },
        "archive": {"retention_days": 30},
        "heuristics": {
            "multi_source_max_boost": 2, "recency_window_hours": 2,
            "recency_boost": 1, "source_weight_max_boost": 1,
        },
        "categories": {
            "order": ["политика"],
            "emoji": {"политика": "🏛️"},
            "labels": {"политика": "🏛️ Политика"},
        },
    }


def test_validate_config_accepts_well_formed():
    _validate_config(_good_config())  # should not raise


def test_validate_config_rejects_missing_section():
    cfg = _good_config()
    del cfg["llm"]
    with pytest.raises(SystemExit, match="missing section 'llm'"):
        _validate_config(cfg)


def test_validate_config_rejects_missing_nested_key():
    cfg = _good_config()
    del cfg["llm"]["classify_concurrency"]
    with pytest.raises(SystemExit, match="llm.classify_concurrency"):
        _validate_config(cfg)


def test_validate_config_rejects_wrong_type():
    cfg = _good_config()
    cfg["llm"]["classify_concurrency"] = "four"
    with pytest.raises(SystemExit, match="llm.classify_concurrency"):
        _validate_config(cfg)


def test_validate_config_rejects_missing_category_emoji():
    cfg = _good_config()
    cfg["categories"]["order"].append("спорт")
    with pytest.raises(SystemExit, match="categories.emoji missing entry for 'спорт'"):
        _validate_config(cfg)


def test_validate_config_rejects_non_mapping_top_level():
    with pytest.raises(SystemExit, match="top level must be a mapping"):
        _validate_config(["not", "a", "mapping"])
