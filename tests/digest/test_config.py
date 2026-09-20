"""Tests for news_digest.config.Config — construction and validation."""

import pytest

from news_digest.config import Config, ConfigError


def test_loads_well_formed(raw_config):
    cfg = Config.from_dict(raw_config)
    assert cfg.llm.active == "cloud"
    assert cfg.pipeline.chunk_size == 15
    assert cfg.dedup.semantic_threshold == 0.7


def test_active_provider_resolves(raw_config):
    cfg = Config.from_dict(raw_config)
    assert cfg.llm.active_provider.model == "m"
    assert cfg.llm.active_provider.api_key_env == "OLLAMA_API_KEY"


def test_active_local_provider(raw_config):
    raw_config["llm"]["active"] = "local"
    cfg = Config.from_dict(raw_config)
    assert cfg.llm.active_provider.api_key_env is None


def test_top_level_must_be_mapping():
    with pytest.raises(ConfigError, match="top level must be a mapping"):
        Config.from_dict(["not", "a", "mapping"])


def test_missing_section_rejected(raw_config):
    del raw_config["llm"]
    with pytest.raises(ConfigError, match="section 'llm'"):
        Config.from_dict(raw_config)


def test_missing_nested_key_rejected(raw_config):
    del raw_config["pipeline"]["chunk_size"]
    with pytest.raises(ConfigError, match="pipeline.*chunk_size"):
        Config.from_dict(raw_config)


def test_active_must_match_provider(raw_config):
    raw_config["llm"]["active"] = "nope"
    with pytest.raises(ConfigError, match="llm.active 'nope'"):
        Config.from_dict(raw_config)


def test_empty_providers_rejected(raw_config):
    raw_config["llm"]["providers"] = []
    with pytest.raises(ConfigError):
        Config.from_dict(raw_config)


def test_category_without_emoji_rejected(raw_config):
    raw_config["categories"]["order"].append("спорт")
    with pytest.raises(ConfigError, match="categories.emoji missing entry for 'спорт'"):
        Config.from_dict(raw_config)


def test_categories_helpers(raw_config):
    cfg = Config.from_dict(raw_config)
    assert cfg.categories.emoji_for("политика") == "🏛️"
    assert cfg.categories.label_for("мир") == "🌍 Мир"
    assert cfg.categories.emoji_for("unknown") == "📌"


def test_real_config_yaml_loads():
    """The shipped config.yaml must satisfy the schema."""
    cfg = Config.load()
    assert cfg.llm.providers
    assert "политика" in cfg.categories.order
