"""Tests for news_digest.jsonio — atomic save_json, load_json, load_env_secret."""

import os

import pytest

from news_digest.jsonio import load_env_secret, load_json, save_json


def test_save_json_roundtrip(tmp_path):
    path = str(tmp_path / "out.json")
    save_json(path, {"a": [1, 2], "б": "юникод"})
    assert load_json(path) == {"a": [1, 2], "б": "юникод"}


def test_save_json_leaves_no_tmp_file(tmp_path):
    path = str(tmp_path / "out.json")
    save_json(path, {"a": 1})
    assert os.listdir(tmp_path) == ["out.json"]


def test_save_json_overwrites_existing(tmp_path):
    path = str(tmp_path / "out.json")
    save_json(path, {"version": 1})
    save_json(path, {"version": 2})
    assert load_json(path) == {"version": 2}


def test_save_json_failed_write_preserves_original(tmp_path):
    path = str(tmp_path / "out.json")
    save_json(path, {"version": 1})
    with pytest.raises(TypeError):
        save_json(path, {"bad": object()})
    assert load_json(path) == {"version": 1}
    assert os.listdir(tmp_path) == ["out.json"]


def test_load_env_secret_prefers_process_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MY_KEY", "from_env")
    env = tmp_path / ".env"
    env.write_text('MY_KEY="from_file"\n', encoding="utf-8")
    assert load_env_secret("MY_KEY", str(env)) == "from_env"


def test_load_env_secret_falls_back_to_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("MY_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("MY_KEY='bare'\n", encoding="utf-8")
    assert load_env_secret("MY_KEY", str(env)) == "bare"


def test_load_env_secret_missing_returns_empty(monkeypatch, tmp_path):
    monkeypatch.delenv("MY_KEY", raising=False)
    assert load_env_secret("MY_KEY", str(tmp_path / ".env")) == ""
