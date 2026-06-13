"""Tests for call_ollama: cache-key namespace and _classify_once robustness."""

import json

import httpx
import pytest

import call_ollama
from call_ollama import _chunk_hash, _classify_once

ARTICLES = [{"title": "t", "url": "http://a", "source": "s"}]


# ── cache key ───────────────────────────────────────────────────────

def test_chunk_hash_is_deterministic():
    assert _chunk_hash(ARTICLES) == _chunk_hash(ARTICLES)


def test_chunk_hash_changes_with_articles():
    other = [{"title": "different", "url": "http://b", "source": "s"}]
    assert _chunk_hash(ARTICLES) != _chunk_hash(other)


def test_chunk_hash_changes_with_namespace():
    """Switching model/provider/prompt must invalidate cached classifications."""
    a = _chunk_hash(ARTICLES, namespace="cloud|model-a|0.3|prompthash")
    b = _chunk_hash(ARTICLES, namespace="cloud|model-b|0.3|prompthash")
    c = _chunk_hash(ARTICLES, namespace="cloud|model-a|0.3|otherprompt")
    assert len({a, b, c}) == 3


def test_default_namespace_includes_active_model():
    assert call_ollama.MODEL in call_ollama._CACHE_NAMESPACE
    assert call_ollama.PROVIDER_NAME in call_ollama._CACHE_NAMESPACE


# ── _classify_once HTTP behavior ────────────────────────────────────

@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(call_ollama.time, "sleep", lambda s: None)


def _install_transport(monkeypatch, handler):
    """Make call_ollama's httpx.Client use a MockTransport; returns request counter."""
    calls = {"n": 0}
    real_client = httpx.Client

    def counting_handler(request):
        calls["n"] += 1
        return handler(request)

    def fake_client(**kwargs):
        return real_client(transport=httpx.MockTransport(counting_handler))

    monkeypatch.setattr(call_ollama.httpx, "Client", fake_client)
    return calls


def _envelope(content: str, finish_reason: str = "stop") -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


def test_success_returns_parsed_stories(monkeypatch, no_sleep):
    body = _envelope(json.dumps({"stories": [{"title": "x"}]}))
    _install_transport(monkeypatch, lambda req: httpx.Response(200, json=body))
    parsed = _classify_once(1, {}, "prompt")
    assert parsed == {"stories": [{"title": "x"}]}


def test_non_retryable_4xx_fails_fast(monkeypatch, no_sleep):
    calls = _install_transport(
        monkeypatch, lambda req: httpx.Response(401, text="bad key"))
    assert _classify_once(1, {}, "prompt") is None
    assert calls["n"] == 1  # no pointless backoff retries on auth errors


def test_5xx_is_retried(monkeypatch, no_sleep):
    calls = _install_transport(
        monkeypatch, lambda req: httpx.Response(500, text="boom"))
    assert _classify_once(1, {}, "prompt") is None
    assert calls["n"] == call_ollama.MAX_RETRIES


def test_429_then_success_is_retried(monkeypatch, no_sleep):
    responses = [httpx.Response(429, text="slow down"),
                 httpx.Response(200, json=_envelope('{"stories": []}'))]
    _install_transport(monkeypatch, lambda req: responses.pop(0))
    assert _classify_once(1, {}, "prompt") == {"stories": []}


def test_truncated_response_is_rejected(monkeypatch, no_sleep):
    """finish_reason=length means stories were cut off — must not be 'repaired'."""
    body = _envelope('{"stories": [{"title": "x"}', finish_reason="length")
    _install_transport(monkeypatch, lambda req: httpx.Response(200, json=body))
    assert _classify_once(1, {}, "prompt") is None


def test_malformed_envelope_returns_none(monkeypatch, no_sleep):
    _install_transport(
        monkeypatch, lambda req: httpx.Response(200, json={"unexpected": True}))
    assert _classify_once(1, {}, "prompt") is None


def test_non_json_200_returns_none(monkeypatch, no_sleep):
    _install_transport(
        monkeypatch, lambda req: httpx.Response(200, text="<html>proxy page</html>"))
    assert _classify_once(1, {}, "prompt") is None


# ── usage tracking ──────────────────────────────────────────────────

def _reset_usage(monkeypatch):
    monkeypatch.setitem(call_ollama._usage_stats, "prompt_tokens", 0)
    monkeypatch.setitem(call_ollama._usage_stats, "completion_tokens", 0)
    monkeypatch.setitem(call_ollama._usage_stats, "api_calls", 0)


def test_usage_accumulated_from_response(monkeypatch, no_sleep):
    _reset_usage(monkeypatch)
    body = _envelope('{"stories": []}')
    body["usage"] = {"prompt_tokens": 1200, "completion_tokens": 340}
    _install_transport(monkeypatch, lambda req: httpx.Response(200, json=body))
    _classify_once(1, {}, "prompt")
    _classify_once(2, {}, "prompt")
    assert call_ollama._usage_stats == {
        "prompt_tokens": 2400, "completion_tokens": 680, "api_calls": 2}


def test_usage_tolerates_missing_field(monkeypatch, no_sleep):
    _reset_usage(monkeypatch)
    _install_transport(
        monkeypatch, lambda req: httpx.Response(200, json=_envelope('{"stories": []}')))
    _classify_once(1, {}, "prompt")
    assert call_ollama._usage_stats == {
        "prompt_tokens": 0, "completion_tokens": 0, "api_calls": 1}


def test_append_usage_record(monkeypatch, tmp_path):
    _reset_usage(monkeypatch)
    monkeypatch.setitem(call_ollama._usage_stats, "prompt_tokens", 5000)
    monkeypatch.setitem(call_ollama._usage_stats, "api_calls", 4)
    path = tmp_path / "llm_usage.jsonl"
    call_ollama._append_usage_record(chunks_total=4, path=str(path))
    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["chunks_total"] == 4
    assert record["prompt_tokens"] == 5000
    assert record["api_calls"] == 4
    assert record["model"] == call_ollama.MODEL
