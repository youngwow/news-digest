"""Tests for send_telegram.py — delivery, retries, and the disabled no-op."""

import json

import httpx
import pytest

import send_telegram
from send_telegram import deliver, send_alert, send_message


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(send_telegram.time, "sleep", lambda s: None)


def _mock_client(handler):
    calls = {"n": 0, "payloads": []}

    def counting_handler(request):
        calls["n"] += 1
        calls["payloads"].append(json.loads(request.content))
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(counting_handler)), calls


# ── send_message ────────────────────────────────────────────────────

def test_send_message_success():
    client, calls = _mock_client(lambda req: httpx.Response(200, json={"ok": True}))
    assert send_message(client, "tok", "42", "hello") is True
    assert calls["n"] == 1
    assert calls["payloads"][0]["chat_id"] == "42"
    assert calls["payloads"][0]["text"] == "hello"


def test_send_message_429_then_success(no_sleep):
    responses = [
        httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 0}}),
        httpx.Response(200, json={"ok": True}),
    ]
    client, calls = _mock_client(lambda req: responses.pop(0))
    assert send_message(client, "tok", "42", "hello") is True
    assert calls["n"] == 2


def test_send_message_bad_token_fails_fast(no_sleep):
    client, calls = _mock_client(
        lambda req: httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))
    assert send_message(client, "tok", "42", "hello") is False
    assert calls["n"] == 1  # 4xx is not retried


def test_send_message_5xx_exhausts_retries(no_sleep):
    client, calls = _mock_client(lambda req: httpx.Response(502, text="bad gateway"))
    assert send_message(client, "tok", "42", "hello") is False
    assert calls["n"] == send_telegram.MAX_ATTEMPTS


# ── deliver ─────────────────────────────────────────────────────────

def _write_digest(tmp_path, n_rest: int = 0) -> str:
    digest = {
        "total_unique": 1, "total_raw": 1,
        "digest": {
            "date": "01.06.2026", "headline": "Заголовок",
            "top5": [{"title": "История", "url": "http://example.com/1"}],
            "rubrics": {}, "rest": [f"новость {i}" for i in range(n_rest)],
        },
    }
    path = tmp_path / "digest.json"
    path.write_text(json.dumps(digest, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _enable(monkeypatch, token="tok", chat="42"):
    monkeypatch.setitem(send_telegram.CONFIG["telegram"], "enabled", True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", chat)


def _install_transport(monkeypatch, handler):
    calls = {"n": 0, "texts": []}
    real_client = httpx.Client

    def counting_handler(request):
        calls["n"] += 1
        calls["texts"].append(json.loads(request.content)["text"])
        return handler(request)

    monkeypatch.setattr(send_telegram.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(counting_handler)))
    return calls


def test_deliver_noop_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setitem(send_telegram.CONFIG["telegram"], "enabled", False)
    calls = _install_transport(monkeypatch, lambda req: httpx.Response(200, json={"ok": True}))
    assert deliver(_write_digest(tmp_path)) == 0
    assert calls["n"] == 0  # no HTTP at all


def test_deliver_fails_without_credentials(monkeypatch, tmp_path):
    monkeypatch.setitem(send_telegram.CONFIG["telegram"], "enabled", True)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(send_telegram, "load_env_secret", lambda name: "")
    assert deliver(_write_digest(tmp_path)) == 1


def test_deliver_fails_when_digest_missing(monkeypatch, tmp_path):
    _enable(monkeypatch)
    assert deliver(str(tmp_path / "nope.json")) == 1


def test_deliver_sends_single_part(monkeypatch, tmp_path):
    _enable(monkeypatch)
    calls = _install_transport(monkeypatch, lambda req: httpx.Response(200, json={"ok": True}))
    assert deliver(_write_digest(tmp_path)) == 0
    assert calls["n"] == 1
    assert "История" in calls["texts"][0]
    assert not calls["texts"][0].startswith("[1/")  # no part prefix for single message


def test_deliver_numbers_multipart_messages(monkeypatch, tmp_path):
    _enable(monkeypatch)
    calls = _install_transport(monkeypatch, lambda req: httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(send_telegram, "split_message", lambda text: ["часть А", "часть Б"])
    assert deliver(_write_digest(tmp_path)) == 0
    assert calls["n"] == 2
    assert calls["texts"][0].startswith("[1/2]\n")
    assert calls["texts"][1].startswith("[2/2]\n")


def test_deliver_stops_on_failed_part(monkeypatch, tmp_path, no_sleep):
    _enable(monkeypatch)
    calls = _install_transport(monkeypatch, lambda req: httpx.Response(400, text="Bad Request"))
    monkeypatch.setattr(send_telegram, "split_message", lambda text: ["часть А", "часть Б"])
    assert deliver(_write_digest(tmp_path)) == 1
    assert calls["n"] == 1  # aborted after the first failed part


# ── send_alert ──────────────────────────────────────────────────────

def _enable_alerts(monkeypatch):
    monkeypatch.setitem(send_telegram.CONFIG["telegram"], "alerts", True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")


def test_alert_noop_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setitem(send_telegram.CONFIG["telegram"], "alerts", False)
    calls = _install_transport(monkeypatch, lambda req: httpx.Response(200, json={"ok": True}))
    assert send_alert("ALERT: что-то сломалось", str(tmp_path / "h")) == 0
    assert calls["n"] == 0


def test_alert_sends_and_records_hash(monkeypatch, tmp_path):
    _enable_alerts(monkeypatch)
    calls = _install_transport(monkeypatch, lambda req: httpx.Response(200, json={"ok": True}))
    hash_path = tmp_path / "h"
    assert send_alert("ALERT: что-то сломалось", str(hash_path)) == 0
    assert calls["n"] == 1
    assert "сломалось" in calls["texts"][0]
    assert hash_path.exists()


def test_identical_alert_suppressed(monkeypatch, tmp_path):
    _enable_alerts(monkeypatch)
    calls = _install_transport(monkeypatch, lambda req: httpx.Response(200, json={"ok": True}))
    hash_path = str(tmp_path / "h")
    assert send_alert("ALERT: одно и то же", hash_path) == 0
    assert send_alert("ALERT: одно и то же", hash_path) == 0
    assert calls["n"] == 1  # second send suppressed


def test_changed_alert_sends_again(monkeypatch, tmp_path):
    _enable_alerts(monkeypatch)
    calls = _install_transport(monkeypatch, lambda req: httpx.Response(200, json={"ok": True}))
    hash_path = str(tmp_path / "h")
    send_alert("ALERT: проблема А", hash_path)
    send_alert("ALERT: проблема Б", hash_path)
    assert calls["n"] == 2


def test_empty_alert_is_noop(monkeypatch, tmp_path):
    _enable_alerts(monkeypatch)
    calls = _install_transport(monkeypatch, lambda req: httpx.Response(200, json={"ok": True}))
    assert send_alert("   \n", str(tmp_path / "h")) == 0
    assert calls["n"] == 0


def test_alert_without_credentials_fails(monkeypatch, tmp_path):
    monkeypatch.setitem(send_telegram.CONFIG["telegram"], "alerts", True)
    monkeypatch.setattr(send_telegram, "load_env_secret", lambda name: "")
    assert send_alert("ALERT: x", str(tmp_path / "h")) == 1
