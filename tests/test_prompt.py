"""Tests for ChunkClassifier prompt template loading + per-provider override."""

import httpx
import pytest
from support import valid_raw_config

from news_digest.classify.classifier import ChunkClassifier
from news_digest.config import Config
from news_digest.paths import ProjectPaths


def _paths(tmp_path) -> ProjectPaths:
    (tmp_path / "data").mkdir(exist_ok=True)
    return ProjectPaths.from_root(str(tmp_path))


def _config(active="local") -> Config:
    raw = valid_raw_config()
    raw["llm"]["active"] = active
    return Config.from_dict(raw)


_DUMMY = httpx.MockTransport(lambda r: httpx.Response(200))


def test_default_template_loads_with_placeholders(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "classify.txt").write_text("{n_articles}\n{articles_json}",
                                                       encoding="utf-8")
    clf = ChunkClassifier(_config(), _paths(tmp_path), transport=_DUMMY)
    assert clf.prompt_path.endswith("classify.txt")
    assert "{n_articles}" in clf.prompt_template
    assert "{articles_json}" in clf.prompt_template


def test_picks_provider_override(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "classify.txt").write_text("default", encoding="utf-8")
    (tmp_path / "prompts" / "classify.local.txt").write_text("override", encoding="utf-8")
    clf = ChunkClassifier(_config("local"), _paths(tmp_path), transport=_DUMMY)
    assert clf.prompt_path.endswith("classify.local.txt")
    assert clf.prompt_template == "override"


def test_falls_back_to_default(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "classify.txt").write_text("default", encoding="utf-8")
    clf = ChunkClassifier(_config("local"), _paths(tmp_path), transport=_DUMMY)
    assert clf.prompt_path.endswith("classify.txt")


def test_missing_default_raises(tmp_path):
    (tmp_path / "prompts").mkdir()
    with pytest.raises(FileNotFoundError, match="missing classification prompt"):
        ChunkClassifier(_config(), _paths(tmp_path), transport=_DUMMY)
