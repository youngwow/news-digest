"""Сторож (`watchdog`) и запуск для cron (`run`): пороги из config.yaml, ALERT-блок,
подавление повторов, lock и гейтинг шагов."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from support import MockRoutes

from src import cli
from src.delivery.telegram import TelegramBot
from src.models import CollectReport, DigestDelivery, FetchState, Source
from src.services.pipeline_run import PipelineRun, RunLock
from src.services.watchdog import CRITICAL, WARNING, Watchdog

NOW = datetime(2026, 9, 20, 7, 0, tzinfo=timezone.utc)
SEND_URL = "https://api.telegram.org/bottoken/sendMessage"


@pytest.fixture
def clock(monkeypatch):
    import src.services.watchdog as watchdog_mod

    monkeypatch.setattr(watchdog_mod, "utc_now", lambda: NOW)
    return NOW


def _collect(db, *, minutes_ago: int, ok: int = 5, fail: int = 0) -> None:
    finished = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    db.runs.add(
        CollectReport(started_at=finished, finished_at=finished, sources_ok=ok, sources_fail=fail, docs_new=3)
    )


def _processing(db, *, processed: int = 4, degraded: int = 0, failed: int = 0, status: str = "done") -> None:
    run = db.processing_runs.start({}, trigger="cli")
    if status == "failed":
        db.processing_runs.fail(run.id, "модель вернула 500")
    else:
        db.processing_runs.finish(
            run.id, {"processed": processed, "documents": processed, "degraded": degraded, "failed": failed}
        )


def _watchdog(config, db, routes=None, monkeypatch=None) -> tuple[Watchdog, MockRoutes | None]:
    table = None
    bot = None
    if routes is not None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
        table = MockRoutes(routes)
        bot = TelegramBot(config.telegram, transport=httpx.MockTransport(table.handler), sleep=lambda s: None)
    return Watchdog(config, db, bot=bot), table


# ── checks ─────────────────────────────────────────────────────────────────


def test_fresh_install_is_critical_because_nothing_ran(config, db, clock):
    report = _watchdog(config, db)[0].run_checks()
    assert report.overall == CRITICAL
    assert report.to_dict()["checks"]["collect"]["issues"][0]["message"].startswith("сбор ещё ни разу")
    assert report.to_dict()["checks"]["processing"]["status"] == "healthy"  # очередь пуста


def test_recent_collect_and_clean_processing_are_healthy(config, db, clock):
    _collect(db, minutes_ago=30)
    _processing(db)
    report = _watchdog(config, db)[0].run_checks()
    assert report.overall == "healthy"
    assert report.to_dict()["checks"]["collect"]["details"]["age_minutes"] == 30


def test_stale_collect_is_critical_at_the_configured_age(config, db, clock):
    _collect(db, minutes_ago=config.health.max_collect_age_minutes + 1)
    _processing(db)
    checks = {c.name: c for c in _watchdog(config, db)[0].run_checks().checks}
    assert checks["collect"].status == CRITICAL
    assert "порог 360" in checks["collect"].issues[0][1]


def test_few_answering_sources_warn_and_none_is_critical(config, db, clock):
    _collect(db, minutes_ago=5, ok=2, fail=3)
    checks = {c.name: c for c in _watchdog(config, db)[0].run_checks().checks}
    assert checks["collect"].status == WARNING and "2 из 5" in checks["collect"].issues[0][1]
    _collect(db, minutes_ago=1, ok=0, fail=5)
    checks = {c.name: c for c in _watchdog(config, db)[0].run_checks().checks}
    assert checks["collect"].status == CRITICAL


def test_a_source_failing_repeatedly_is_reported_by_name(config, db, clock):
    source = db.sources.add(Source(name="Хабр", url="https://t.me/habr_com", kind="telegram", fetch_url="https://t.me/s/habr_com"))
    db.fetch_state.save(FetchState(source_id=source.id, consecutive_failures=5, last_error="timeout"))
    _collect(db, minutes_ago=5)
    checks = {c.name: c for c in _watchdog(config, db)[0].run_checks().checks}
    assert checks["sources"].status == WARNING
    assert checks["sources"].issues == [(WARNING, "Хабр: 5 неудач подряд (timeout)")]


def test_processing_failures_and_degradation_are_graded(config, db, clock):
    _collect(db, minutes_ago=5)
    _processing(db, status="failed")
    checks = {c.name: c for c in _watchdog(config, db)[0].run_checks().checks}
    assert checks["processing"].status == CRITICAL and "упал" in checks["processing"].issues[0][1]

    _processing(db, processed=4, degraded=4)
    checks = {c.name: c for c in _watchdog(config, db)[0].run_checks().checks}
    assert checks["processing"].status == CRITICAL and "модель недоступна" in checks["processing"].issues[0][1]

    _processing(db, processed=4, degraded=1, failed=1)
    checks = {c.name: c for c in _watchdog(config, db)[0].run_checks().checks}
    assert [s for s, _ in checks["processing"].issues] == [WARNING, WARNING]


def test_backlog_over_the_threshold_warns(config, db, clock, monkeypatch):
    from dataclasses import replace

    _collect(db, minutes_ago=5)
    monkeypatch.setattr(db.documents, "count_unprocessed", lambda: 301)
    watchdog = Watchdog(replace(config), db)
    checks = {c.name: c for c in watchdog.run_checks().checks}
    assert "без карточки: 301" in checks["processing"].issues[0][1]


def test_delivery_is_checked_only_when_delivery_is_enabled(config, db, clock):
    from dataclasses import replace

    _collect(db, minutes_ago=5)
    _processing(db)
    assert "delivery" not in {c.name for c in _watchdog(config, db)[0].run_checks().checks}

    enabled = replace(config, telegram=replace(config.telegram, deliver=True))
    checks = {c.name: c for c in Watchdog(enabled, db).run_checks().checks}
    assert checks["delivery"].status == WARNING and "ни разу" in checks["delivery"].issues[0][1]

    db.deliveries.add(DigestDelivery(sent_at=(NOW - timedelta(minutes=10)).isoformat(), chat_id="42"), [])
    checks = {c.name: c for c in Watchdog(enabled, db).run_checks().checks}
    assert checks["delivery"].status == "healthy"
    db.deliveries.add(DigestDelivery(sent_at=(NOW - timedelta(minutes=10)).isoformat(), chat_id="42"), [])


# ── alert ──────────────────────────────────────────────────────────────────


def test_alert_block_lists_only_unhealthy_checks(config, db, clock):
    _collect(db, minutes_ago=999)
    _processing(db)
    watchdog = _watchdog(config, db)[0]
    alert = watchdog.build_alert(watchdog.run_checks())
    assert alert.startswith("ALERT: ⚠️ news-digest — проблемы конвейера\n\nALERT: ❌ Сбор источников: critical\nALERT:   [КРИТ] последний сбор был 999 мин назад")
    assert "Обработка" not in alert
    assert alert.endswith("— проверено 2026-09-20 07:00 UTC")


def test_run_is_silent_when_healthy_and_alerts_once_when_not(config, db, clock, tmp_path, capsys, monkeypatch):
    from dataclasses import replace

    _collect(db, minutes_ago=5)
    _processing(db)
    watchdog, table = _watchdog(config, db, {SEND_URL: (200, b"{}", {})}, monkeypatch)
    watchdog.config = replace(config, telegram=replace(config.telegram, alerts=True))
    hash_path = str(tmp_path / ".last_alert_hash")

    assert watchdog.run(hash_path)[0] == 0
    assert capsys.readouterr().out == "" and table.requests == []

    _collect(db, minutes_ago=5, ok=0, fail=3)
    assert watchdog.run(hash_path)[0] == 1
    assert "ALERT:" in capsys.readouterr().out and len(table.requests) == 1
    assert watchdog.run(hash_path)[0] == 1  # тот же алерт — печать есть, повтора в чат нет
    assert len(table.requests) == 1


def test_run_without_alerts_enabled_never_touches_telegram(config, db, clock, tmp_path, capsys, monkeypatch):
    watchdog, table = _watchdog(config, db, {SEND_URL: (200, b"{}", {})}, monkeypatch)
    assert watchdog.run(str(tmp_path / "h"))[0] == 1
    assert table.requests == []


def test_cli_watchdog_json_and_exit_codes(config, hub_paths, file_db, clock, capsys):
    args = cli.build_parser().parse_args(["watchdog", "--json"])
    assert cli._cmd_watchdog(args, config, hub_paths) == 1
    assert json.loads(capsys.readouterr().out)["overall"] == CRITICAL
    _collect(file_db, minutes_ago=1)
    _processing(file_db)
    assert cli._cmd_watchdog(cli.build_parser().parse_args(["watchdog"]), config, hub_paths) == 0
    assert capsys.readouterr().out == ""


# ── lock ───────────────────────────────────────────────────────────────────


def test_lock_is_exclusive_and_released(tmp_path):
    path = str(tmp_path / "data" / ".run.lock")
    lock = RunLock(path)
    assert lock.acquire() and not RunLock(path).acquire()
    lock.release()
    assert RunLock(path).acquire()


def test_stale_lock_is_broken(tmp_path):
    path = str(tmp_path / ".run.lock")
    os.mkdir(path)
    old = time.time() - 7 * 3600
    os.utime(path, (old, old))
    assert RunLock(path).acquire()


# ── run: гейтинг ───────────────────────────────────────────────────────────


def _run(config, paths, **steps) -> tuple:
    calls: list[str] = []

    def make(name, outcome):
        def step():
            calls.append(name)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return step

    result = PipelineRun(config, paths).run(
        collect=make("collect", steps.get("collect", ("ok", "5 источников"))),
        process=make("process", steps.get("process", ("ok", "+3 карточки"))),
        digest=make("digest", steps.get("digest", ("ok", "отправлено"))),
        do_collect=steps.get("do_collect", True),
        do_process=steps.get("do_process", True),
        do_digest=steps.get("do_digest", True),
    )
    return result, calls


def test_clean_run_executes_every_step_and_writes_status(config, hub_paths):
    result, calls = _run(config, hub_paths)
    assert calls == ["collect", "process", "digest"] and result.exit_code == 0
    status = json.loads(open(hub_paths.data("pipeline_status.json"), encoding="utf-8").read())
    assert [s["status"] for s in status["steps"]] == ["ok", "ok", "ok"] and status["exit_code"] == 0
    assert not os.path.exists(hub_paths.data(".run.lock"))


def test_failed_collect_skips_processing_and_digest(config, hub_paths):
    result, calls = _run(config, hub_paths, collect=("failed", "ни один источник не ответил"))
    assert calls == ["collect"] and result.exit_code == 1
    assert [s.status for s in result.steps] == ["failed", "skipped", "skipped"]


def test_an_exception_in_a_step_is_a_failure_and_the_lock_is_released(config, hub_paths):
    result, calls = _run(config, hub_paths, process=RuntimeError("boom"))
    assert calls == ["collect", "process"] and result.exit_code == 1
    assert result.steps[1].detail == "RuntimeError: boom" and result.steps[2].status == "skipped"
    assert not os.path.exists(hub_paths.data(".run.lock"))


def test_degraded_processing_still_produces_the_digest(config, hub_paths):
    result, calls = _run(config, hub_paths, process=("degraded", "модель недоступна"))
    assert calls == ["collect", "process", "digest"] and result.exit_code == 2


def test_flags_skip_steps_without_gating(config, hub_paths):
    result, calls = _run(config, hub_paths, do_collect=False, do_process=False)
    assert calls == ["digest"] and result.exit_code == 0
    assert [s.status for s in result.steps] == ["skipped", "skipped", "ok"]


def test_a_second_run_under_the_lock_is_a_noop(config, hub_paths):
    lock = RunLock(hub_paths.data(".run.lock"))
    assert lock.acquire()
    try:
        result, calls = _run(config, hub_paths)
    finally:
        lock.release()
    assert result.locked and calls == [] and result.exit_code == 0


def test_cli_run_prints_the_digest_and_records_it_when_delivery_is_off(config, hub_paths, file_db, item_factory, monkeypatch, capsys):
    from src.models import RawDocument

    source = file_db.sources.add(Source(name="Хабр", url="https://t.me/habr_com", kind="telegram", fetch_url="https://t.me/s/habr_com"))
    with file_db.transaction():
        doc_id = file_db.documents.insert(
            RawDocument(source_id=source.id, external_id="1", url="https://t.me/habr_com/1", title="Пост", fetched_at=NOW.isoformat())
        )
    item_factory(file_db, doc_id, title="Пост про ИИ")
    args = cli.build_parser().parse_args(["run", "--no-collect", "--no-process"])

    assert cli._cmd_run(args, config, hub_paths) == 0

    captured = capsys.readouterr()
    assert "Пост про ИИ" in captured.out
    assert "digest   ok        напечатано 1 карточек, доставка #1 без отправки" in captured.err
    assert file_db.deliveries.last().trigger == "run"
    assert cli._cmd_run(args, config, hub_paths) == 0  # второй раз — нечего
    assert "empty" in capsys.readouterr().err
