"""Tests for prompt template loading + per-provider override resolution."""

import os

import call_ollama


def test_default_prompt_template_loads():
    assert call_ollama.PROMPT_PATH.endswith("/prompts/classify.txt") or \
        call_ollama.PROMPT_PATH.endswith("\\prompts\\classify.txt") or \
        "classify." in os.path.basename(call_ollama.PROMPT_PATH)
    assert "{n_articles}" in call_ollama.PROMPT_TEMPLATE
    assert "{articles_json}" in call_ollama.PROMPT_TEMPLATE


def test_prompt_template_substitutes_placeholders():
    formatted = call_ollama.PROMPT_TEMPLATE.format(
        n_articles=15, articles_json="[]",
    )
    assert "15" in formatted
    assert "[]" in formatted
    assert "{n_articles}" not in formatted
    assert "{articles_json}" not in formatted


def test_resolve_prompt_picks_override(monkeypatch, tmp_path):
    # Set PROMPTS_DIR to a temp dir, drop both default and override files.
    monkeypatch.setattr(call_ollama, "PROMPTS_DIR", str(tmp_path))
    monkeypatch.setattr(call_ollama, "PROVIDER_NAME", "myprov")

    (tmp_path / "classify.txt").write_text("default")
    (tmp_path / "classify.myprov.txt").write_text("override")

    resolved = call_ollama._resolve_prompt_path()
    assert resolved.endswith("classify.myprov.txt")


def test_resolve_prompt_falls_back_to_default(monkeypatch, tmp_path):
    monkeypatch.setattr(call_ollama, "PROMPTS_DIR", str(tmp_path))
    monkeypatch.setattr(call_ollama, "PROVIDER_NAME", "nopov")

    (tmp_path / "classify.txt").write_text("default")

    resolved = call_ollama._resolve_prompt_path()
    assert resolved.endswith("classify.txt")
    assert "nopov" not in resolved


def test_resolve_prompt_missing_default_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(call_ollama, "PROMPTS_DIR", str(tmp_path))
    monkeypatch.setattr(call_ollama, "PROVIDER_NAME", "anything")
    import pytest
    with pytest.raises(SystemExit, match="missing classification prompt"):
        call_ollama._resolve_prompt_path()
