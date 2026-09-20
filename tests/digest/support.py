"""Plain (non-fixture) helpers of the news_digest suite, importable as
`from digest.support import ...`.

tests/digest/ is a package (it has an __init__.py) so that its modules never clash
with the platform suite's namesakes (tests/unit/test_dedup.py and friends); pytest
puts tests/ on sys.path, which makes `digest.support` importable.
"""


class FakeEncoder:
    """Maps each text to a fixed vector — for deterministic dedup/threading tests."""

    def __init__(self, vectors_by_text: dict[str, list[float]]):
        self._v = vectors_by_text

    def encode(self, texts: list[str]):
        return [self._v[t] for t in texts]


def valid_raw_config() -> dict:
    """A minimal well-formed raw config dict (mirrors config.yaml structure)."""
    return {
        "llm": {
            "active": "cloud",
            "classify_concurrency": 4,
            "cache_retention_days": 7,
            "failure_tolerance": 0.34,
            "bad_json_retry": True,
            "providers": [
                {"name": "cloud", "base_url": "https://x", "model": "m",
                 "temperature": 0.3, "max_tokens": 1024, "timeout": 30,
                 "max_retries": 3, "api_key_env": "OLLAMA_API_KEY"},
                {"name": "local", "base_url": "http://localhost:11434/v1",
                 "model": "gemma", "temperature": 0.3, "max_tokens": 1024,
                 "timeout": 300, "max_retries": 2, "api_key_env": None},
            ],
        },
        "scraper": {"date_window_hours": 8, "request_timeout": 20, "max_redirects": 5,
                    "user_agent": "x", "fetch_bodies": False, "body_max_chars": 3000,
                    "body_timeout": 10, "body_concurrency": 8, "body_cache_retention_days": 14},
        "pipeline": {"chunk_size": 15, "training_log": True},
        "telegram": {"message_limit": 4096, "enabled": False, "alerts": False},
        "health": {"min_sources_ok": 3, "expected_sources": 10, "default_max_age_minutes": 360},
        "dedup": {"overlap_threshold": 0.5, "shared_words_min": 5, "containment_min_len": 15,
                  "cross_run_enabled": True, "cross_run_retention_days": 1,
                  "semantic_enabled": False, "semantic_threshold": 0.7,
                  "semantic_model": "m", "semantic_model_static": "m2"},
        "archive": {"retention_days": 30},
        "threads": {"enabled": False, "lookback_days": 3, "similarity_threshold": 0.6},
        "heuristics": {"multi_source_max_boost": 2, "recency_window_hours": 2,
                       "recency_boost": 1, "source_weight_max_boost": 1},
        "categories": {"order": ["политика", "мир"],
                       "emoji": {"политика": "🏛️", "мир": "🌍"},
                       "labels": {"политика": "🏛️ Политика", "мир": "🌍 Мир"}},
    }
