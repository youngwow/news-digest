"""Tests for news_digest.classify — LLMClient HTTP behavior, cache key, classifier."""

import json

import httpx
from support import valid_raw_config

from news_digest.classify.cache import ChunkCache
from news_digest.classify.classifier import ChunkClassifier
from news_digest.classify.client import CompletionResult, LLMClient, UsageTracker
from news_digest.config import Config, Provider
from news_digest.paths import ProjectPaths


def _provider(max_retries=3):
    return Provider(name="cloud", base_url="https://x", model="m", temperature=0.3,
                    max_tokens=1024, timeout=30, max_retries=max_retries, api_key_env=None)


def _client(handler, max_retries=3, usage=None):
    calls = {"n": 0, "payloads": []}

    def counting(request):
        calls["n"] += 1
        calls["payloads"].append(json.loads(request.content))
        return handler(request)

    client = LLMClient(_provider(max_retries), usage=usage,
                       transport=httpx.MockTransport(counting))
    return client, calls


def _envelope(content, finish_reason="stop", usage=None):
    env = {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
    if usage is not None:
        env["usage"] = usage
    return env


_NO_SLEEP = lambda s: None  # noqa: E731


# ── LLMClient.complete ──────────────────────────────────────────────

def test_success_returns_content():
    body = _envelope('{"stories": []}')
    client, calls = _client(lambda r: httpx.Response(200, json=body))
    res = client.complete("c1", "prompt", sleep=_NO_SLEEP)
    assert res.content == '{"stories": []}'
    assert calls["n"] == 1
    assert calls["payloads"][0]["model"] == "m"


def test_non_retryable_4xx_fails_fast():
    client, calls = _client(lambda r: httpx.Response(401, text="bad key"))
    res = client.complete("c1", "p", sleep=_NO_SLEEP)
    assert res.content is None and res.error == "http"
    assert calls["n"] == 1


def test_5xx_exhausts_retries():
    client, calls = _client(lambda r: httpx.Response(500, text="boom"), max_retries=3)
    assert client.complete("c1", "p", sleep=_NO_SLEEP).content is None
    assert calls["n"] == 3


def test_429_then_success_is_retried():
    responses = [httpx.Response(429, text="slow"), httpx.Response(200, json=_envelope('{"stories": []}'))]
    client, calls = _client(lambda r: responses.pop(0))
    assert client.complete("c1", "p", sleep=_NO_SLEEP).content == '{"stories": []}'
    assert calls["n"] == 2


def test_truncated_response_is_rejected():
    body = _envelope('{"stories": [{"title": "x"', finish_reason="length")
    client, _ = _client(lambda r: httpx.Response(200, json=body))
    res = client.complete("c1", "p", sleep=_NO_SLEEP)
    assert res.content is None and res.error == "truncated"


def test_malformed_envelope_returns_none():
    client, _ = _client(lambda r: httpx.Response(200, json={"unexpected": True}))
    res = client.complete("c1", "p", sleep=_NO_SLEEP)
    assert res.content is None and res.error == "malformed"


def test_non_json_200_returns_none():
    client, _ = _client(lambda r: httpx.Response(200, text="<html>proxy</html>"))
    assert client.complete("c1", "p", sleep=_NO_SLEEP).content is None


def test_usage_accumulated():
    usage = UsageTracker()
    body = _envelope('{"stories": []}', usage={"prompt_tokens": 1200, "completion_tokens": 340})
    client, _ = _client(lambda r: httpx.Response(200, json=body), usage=usage)
    client.complete("c1", "p", sleep=_NO_SLEEP)
    client.complete("c2", "p", sleep=_NO_SLEEP)
    assert usage.snapshot() == {"prompt_tokens": 2400, "completion_tokens": 680, "api_calls": 2}


def test_usage_tolerates_missing_field():
    usage = UsageTracker()
    client, _ = _client(lambda r: httpx.Response(200, json=_envelope('{}')), usage=usage)
    client.complete("c1", "p", sleep=_NO_SLEEP)
    assert usage.snapshot() == {"prompt_tokens": 0, "completion_tokens": 0, "api_calls": 1}


# ── ChunkCache key namespacing ──────────────────────────────────────

ARTICLES = [{"title": "t", "url": "http://a", "source": "s"}]


def test_cache_key_deterministic(tmp_path):
    c = ChunkCache(str(tmp_path), "ns")
    assert c.key(ARTICLES) == c.key(ARTICLES)


def test_cache_key_changes_with_articles(tmp_path):
    c = ChunkCache(str(tmp_path), "ns")
    assert c.key(ARTICLES) != c.key([{"title": "different"}])


def test_cache_key_changes_with_namespace(tmp_path):
    a = ChunkCache(str(tmp_path), "cloud|model-a|0.3|ph").key(ARTICLES)
    b = ChunkCache(str(tmp_path), "cloud|model-b|0.3|ph").key(ARTICLES)
    assert a != b


def test_cache_put_get_roundtrip(tmp_path):
    c = ChunkCache(str(tmp_path), "ns")
    assert c.get(ARTICLES) is None
    c.put(ARTICLES, {"chunk_size": 1, "stories": [{"title": "x"}]})
    assert c.get(ARTICLES)["stories"][0]["title"] == "x"


# ── ChunkClassifier integration (local provider, mocked transport) ──

def _classifier(tmp_path, handler):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "classify.txt").write_text(
        "Classify {n_articles}\n{articles_json}", encoding="utf-8")
    (tmp_path / "data" / "chunks").mkdir(parents=True)
    raw = valid_raw_config()
    raw["llm"]["active"] = "local"  # api_key_env=None → no secret needed
    cfg = Config.from_dict(raw)
    paths = ProjectPaths.from_root(str(tmp_path))
    transport = httpx.MockTransport(handler) if handler else None
    return ChunkClassifier(cfg, paths, transport=transport), paths


def _write_chunk(paths, n, articles):
    with open(f"{paths.data('chunks')}/chunk_{n}.json", "w", encoding="utf-8") as f:
        json.dump({"chunk_index": n, "articles": articles}, f)


def test_classify_chunk_success_writes_output(tmp_path):
    body = _envelope(json.dumps({"stories": [{"title": "история", "category": "мир",
                                              "importance": 5, "sources": ["s"], "urls": ["u"]}]}))
    clf, paths = _classifier(tmp_path, lambda r: httpx.Response(200, json=body))
    _write_chunk(paths, 1, [{"title": "a", "url": "u", "source": "s"}])
    assert clf.classify_chunk(1) is True
    out = json.load(open(f"{paths.data('chunks')}/chunk_1_classified.json"))
    assert out["stories"][0]["title"] == "история"


def test_classify_chunk_cache_hit_skips_api(tmp_path):
    calls = {"n": 0}

    def handler(r):
        calls["n"] += 1
        return httpx.Response(200, json=_envelope('{"stories": []}'))

    clf, paths = _classifier(tmp_path, handler)
    articles = [{"title": "a", "url": "u", "source": "s"}]
    clf.cache.put(articles, {"chunk_size": 1, "stories": [{"title": "cached"}]})
    _write_chunk(paths, 1, articles)
    assert clf.classify_chunk(1) is True
    assert calls["n"] == 0  # served from cache


def test_classify_all_tolerates_partial_failure(tmp_path):
    # chunk 1 succeeds, chunk 2 gets a hard 400 → 1/2 = 50% > tolerance 0.34 → fail
    def handler(r):
        payload = json.loads(r.content)
        if "FAILME" in payload["messages"][0]["content"]:
            return httpx.Response(400, text="bad")
        return httpx.Response(200, json=_envelope('{"stories": []}'))

    clf, paths = _classifier(tmp_path, handler)
    _write_chunk(paths, 1, [{"title": "ok", "url": "u1", "source": "s"}])
    _write_chunk(paths, 2, [{"title": "FAILME", "url": "u2", "source": "s"}])
    assert clf.classify_all() is False


def test_classify_all_within_tolerance_succeeds(tmp_path):
    # 1 of 4 fails = 25% <= 34% tolerance → overall success
    def handler(r):
        payload = json.loads(r.content)
        if "FAILME" in payload["messages"][0]["content"]:
            return httpx.Response(400, text="bad")
        return httpx.Response(200, json=_envelope('{"stories": []}'))

    clf, paths = _classifier(tmp_path, handler)
    for n in (1, 2, 3):
        _write_chunk(paths, n, [{"title": "ok", "url": f"u{n}", "source": "s"}])
    _write_chunk(paths, 4, [{"title": "FAILME", "url": "u4", "source": "s"}])
    assert clf.classify_all() is True
    # usage record appended
    assert clf.usage_log.read()


def test_completion_result_dataclass():
    assert CompletionResult(None, "http").error == "http"
