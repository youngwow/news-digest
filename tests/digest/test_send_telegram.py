"""Tests for TelegramNotifier — delivery, alerts, retries, no-op gating."""

import json

import httpx

from digest.support import valid_raw_config
from news_digest.config import Config
from news_digest.delivery.telegram import TelegramNotifier
from news_digest.paths import ProjectPaths

_NO_SLEEP = lambda s: None  # noqa: E731


def _notifier(tmp_path, enabled=False, alerts=False, transport=None):
    raw = valid_raw_config()
    raw["telegram"]["enabled"] = enabled
    raw["telegram"]["alerts"] = alerts
    cfg = Config.from_dict(raw)
    return TelegramNotifier(cfg, ProjectPaths.from_root(str(tmp_path)), transport=transport)


def _transport(handler):
    calls = {"n": 0, "texts": []}

    def counting(request):
        calls["n"] += 1
        calls["texts"].append(json.loads(request.content)["text"])
        return handler(request)

    return httpx.MockTransport(counting), calls


def _write_digest(tmp_path):
    (tmp_path / "data").mkdir(exist_ok=True)
    digest = {"total_unique": 1, "total_raw": 1, "digest": {
        "date": "01.06.2026", "headline": "Заголовок",
        "top5": [{"title": "История", "url": "http://example.com/1"}],
        "rubrics": {}, "rest": []}}
    path = tmp_path / "data" / "digest.json"
    path.write_text(json.dumps(digest, ensure_ascii=False), encoding="utf-8")
    return str(path)


# ── send_message ────────────────────────────────────────────────────

def test_send_message_success(tmp_path):
    transport, calls = _transport(lambda r: httpx.Response(200, json={"ok": True}))
    n = _notifier(tmp_path, transport=transport)
    with n._client() as c:
        assert n.send_message(c, "tok", "42", "hello", sleep=_NO_SLEEP) is True
    assert calls["n"] == 1


def test_send_message_429_then_success(tmp_path):
    responses = [httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 0}}),
                 httpx.Response(200, json={"ok": True})]
    transport, calls = _transport(lambda r: responses.pop(0))
    n = _notifier(tmp_path, transport=transport)
    with n._client() as c:
        assert n.send_message(c, "tok", "42", "hi", sleep=_NO_SLEEP) is True
    assert calls["n"] == 2


def test_send_message_bad_token_fails_fast(tmp_path):
    transport, calls = _transport(lambda r: httpx.Response(401, json={"ok": False}))
    n = _notifier(tmp_path, transport=transport)
    with n._client() as c:
        assert n.send_message(c, "tok", "42", "hi", sleep=_NO_SLEEP) is False
    assert calls["n"] == 1


def test_send_message_5xx_exhausts(tmp_path):
    transport, calls = _transport(lambda r: httpx.Response(502, text="bad"))
    n = _notifier(tmp_path, transport=transport)
    with n._client() as c:
        assert n.send_message(c, "tok", "42", "hi", sleep=_NO_SLEEP) is False
    assert calls["n"] == 3


# ── deliver ─────────────────────────────────────────────────────────

def test_deliver_noop_when_disabled(tmp_path):
    transport, calls = _transport(lambda r: httpx.Response(200, json={"ok": True}))
    n = _notifier(tmp_path, enabled=False, transport=transport)
    assert n.deliver(_write_digest(tmp_path)) == 0
    assert calls["n"] == 0


def test_deliver_fails_without_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    n = _notifier(tmp_path, enabled=True)
    assert n.deliver(_write_digest(tmp_path)) == 1


def test_deliver_sends_single_part(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    transport, calls = _transport(lambda r: httpx.Response(200, json={"ok": True}))
    n = _notifier(tmp_path, enabled=True, transport=transport)
    assert n.deliver(_write_digest(tmp_path)) == 0
    assert calls["n"] == 1
    assert "История" in calls["texts"][0]


def test_deliver_fails_when_digest_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    n = _notifier(tmp_path, enabled=True)
    assert n.deliver(str(tmp_path / "data" / "nope.json")) == 1


# ── send_alert ──────────────────────────────────────────────────────

def test_alert_noop_when_disabled(tmp_path):
    transport, calls = _transport(lambda r: httpx.Response(200, json={"ok": True}))
    n = _notifier(tmp_path, alerts=False, transport=transport)
    assert n.send_alert("ALERT: x", str(tmp_path / "h")) == 0
    assert calls["n"] == 0


def test_alert_sends_and_records_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    transport, calls = _transport(lambda r: httpx.Response(200, json={"ok": True}))
    n = _notifier(tmp_path, alerts=True, transport=transport)
    hash_path = str(tmp_path / "h")
    assert n.send_alert("ALERT: сломалось", hash_path) == 0
    assert calls["n"] == 1


def test_identical_alert_suppressed(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    transport, calls = _transport(lambda r: httpx.Response(200, json={"ok": True}))
    n = _notifier(tmp_path, alerts=True, transport=transport)
    hash_path = str(tmp_path / "h")
    n.send_alert("ALERT: одно и то же", hash_path)
    n.send_alert("ALERT: одно и то же", hash_path)
    assert calls["n"] == 1


def test_changed_alert_resends(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    transport, calls = _transport(lambda r: httpx.Response(200, json={"ok": True}))
    n = _notifier(tmp_path, alerts=True, transport=transport)
    hash_path = str(tmp_path / "h")
    n.send_alert("ALERT: А", hash_path)
    n.send_alert("ALERT: Б", hash_path)
    assert calls["n"] == 2


def test_empty_alert_is_noop(tmp_path):
    transport, calls = _transport(lambda r: httpx.Response(200, json={"ok": True}))
    n = _notifier(tmp_path, alerts=True, transport=transport)
    assert n.send_alert("   \n", str(tmp_path / "h")) == 0
    assert calls["n"] == 0
