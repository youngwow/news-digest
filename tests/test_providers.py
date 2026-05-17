"""Tests for the LLM provider abstraction in utils._validate_llm_providers."""

import pytest

from utils import _validate_config


def _good_config() -> dict:
    """Minimal valid config covering all required keys, with multiple providers."""
    return {
        "llm": {
            "active": "cloud",
            "classify_concurrency": 4,
            "cache_retention_days": 7,
            "providers": [
                {
                    "name": "cloud", "base_url": "https://x", "model": "m",
                    "temperature": 0.3, "max_tokens": 1024,
                    "timeout": 30, "max_retries": 3,
                    "api_key_env": "OLLAMA_API_KEY",
                },
                {
                    "name": "local", "base_url": "http://localhost:11434/v1",
                    "model": "gemma3:12b",
                    "temperature": 0.3, "max_tokens": 1024,
                    "timeout": 300, "max_retries": 2,
                    "api_key_env": None,
                },
            ],
        },
        "scraper": {"date_window_hours": 8, "request_timeout": 20,
                    "max_redirects": 5, "user_agent": "x"},
        "pipeline": {"chunk_size": 15},
        "telegram": {"message_limit": 4096},
        "health": {"min_sources_ok": 3, "expected_sources": 10,
                   "default_max_age_minutes": 360},
        "dedup": {"overlap_threshold": 0.3, "shared_words_min": 3,
                  "containment_min_len": 15,
                  "cross_run_enabled": True, "cross_run_retention_days": 3},
        "archive": {"retention_days": 30},
        "heuristics": {"multi_source_max_boost": 2, "recency_window_hours": 2,
                       "recency_boost": 1, "source_weight_max_boost": 1},
        "categories": {"order": ["политика"],
                       "emoji": {"политика": "🏛️"},
                       "labels": {"политика": "🏛️ Политика"}},
    }


def test_accepts_multiple_providers():
    _validate_config(_good_config())  # should not raise


def test_active_local_works_too():
    cfg = _good_config()
    cfg["llm"]["active"] = "local"
    _validate_config(cfg)


def test_active_must_match_a_provider_name():
    cfg = _good_config()
    cfg["llm"]["active"] = "nope"
    with pytest.raises(SystemExit, match="llm.active 'nope'"):
        _validate_config(cfg)


def test_empty_providers_list_rejected():
    cfg = _good_config()
    cfg["llm"]["providers"] = []
    with pytest.raises(SystemExit, match="must contain at least one entry"):
        _validate_config(cfg)


def test_provider_missing_required_key_rejected():
    cfg = _good_config()
    del cfg["llm"]["providers"][0]["base_url"]
    with pytest.raises(SystemExit, match="providers\\[0\\] missing key 'base_url'"):
        _validate_config(cfg)


def test_provider_wrong_type_rejected():
    cfg = _good_config()
    cfg["llm"]["providers"][0]["max_retries"] = "three"
    with pytest.raises(SystemExit, match="providers\\[0\\].max_retries"):
        _validate_config(cfg)


def test_api_key_env_null_accepted():
    """Local Ollama config typically has api_key_env: null."""
    cfg = _good_config()
    cfg["llm"]["active"] = "local"
    _validate_config(cfg)  # local provider has api_key_env: None — must accept


def test_api_key_env_omitted_accepted():
    cfg = _good_config()
    del cfg["llm"]["providers"][1]["api_key_env"]
    _validate_config(cfg)


def test_api_key_env_int_rejected():
    cfg = _good_config()
    cfg["llm"]["providers"][0]["api_key_env"] = 42
    with pytest.raises(SystemExit, match="api_key_env must be string or null"):
        _validate_config(cfg)


def test_providers_list_of_non_dict_rejected():
    cfg = _good_config()
    cfg["llm"]["providers"] = ["not a dict"]
    with pytest.raises(SystemExit, match="providers\\[0\\] must be a mapping"):
        _validate_config(cfg)
