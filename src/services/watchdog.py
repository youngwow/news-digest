"""Watchdog — здоровье конвейера по данным базы и ALERT-блок для cron.

Отдельной системы мониторинга нет: всё, что нужно, уже лежит в `hub.db` —
прогоны сбора и обработки, состояние источников, очередь без карточки,
история доставок. Сторож читает это, сравнивает с порогами `config.yaml →
health` и молчит, пока всё хорошо (cron ничего не шлёт); иначе печатает
русский ALERT-блок и, если включено `telegram.alerts`, отправляет его в чат —
одинаковые подряд алерты подавляются, так что получасовой cron пишет только
об изменениях.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Config
from ..delivery.telegram import DeliveryError, TelegramBot
from ..repositories import Database
from ..utils import get_logger, parse_datetime, utc_now

log = get_logger("watchdog")

CRITICAL = "critical"
WARNING = "warning"

LABELS = {
    "collect": "Сбор источников",
    "sources": "Здоровье источников",
    "processing": "Обработка (модель)",
    "delivery": "Доставка дайджеста",
}


@dataclass
class Check:
    name: str
    issues: list[tuple[str, str]] = field(default_factory=list)  # (severity, message)
    details: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        if not self.issues:
            return "healthy"
        return CRITICAL if any(s == CRITICAL for s, _ in self.issues) else WARNING

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "issues": [{"severity": s, "message": m} for s, m in self.issues],
            "details": self.details,
        }


@dataclass
class Report:
    checks: list[Check]
    checked_at: str

    @property
    def overall(self) -> str:
        statuses = {c.status for c in self.checks}
        if CRITICAL in statuses:
            return CRITICAL
        return WARNING if WARNING in statuses else "healthy"

    def to_dict(self) -> dict:
        return {
            "overall": self.overall,
            "checked_at": self.checked_at,
            "checks": {c.name: c.to_dict() for c in self.checks},
        }


class Watchdog:
    def __init__(self, config: Config, db: Database, bot: TelegramBot | None = None):
        self.config = config
        self.db = db
        self.bot = bot or TelegramBot(config.telegram)

    # ── checks ─────────────────────────────────────────────────────────────

    def run_checks(self) -> Report:
        now = utc_now()
        checks = [
            self.check_collect(now),
            self.check_sources(),
            self.check_processing(),
        ]
        if self.config.telegram.deliver:
            checks.append(self.check_delivery(now))
        return Report(checks=checks, checked_at=now.replace(microsecond=0).isoformat())

    def check_collect(self, now) -> Check:
        check = Check("collect")
        limits = self.config.health
        latest = self.db.runs.latest()
        if latest is None:
            check.issues.append((CRITICAL, "сбор ещё ни разу не запускался"))
            return check
        finished = parse_datetime(latest["finished_at"])
        age = int((now - finished).total_seconds() // 60) if finished else None
        ok, fail = int(latest["sources_ok"]), int(latest["sources_fail"])
        check.details = {
            "last_collect_at": latest["finished_at"],
            "age_minutes": age,
            "sources_ok": ok,
            "sources_fail": fail,
            "docs_new": int(latest["docs_new"]),
        }
        if age is None or age > limits.max_collect_age_minutes:
            check.issues.append(
                (CRITICAL, f"последний сбор был {age if age is not None else '?'} мин назад "
                           f"(порог {limits.max_collect_age_minutes})")
            )
        polled = ok + fail
        if polled and ok / polled < limits.min_sources_ok_share:
            check.issues.append(
                (CRITICAL if ok == 0 else WARNING,
                 f"в последнем сборе ответили {ok} из {polled} источников")
            )
        return check

    def check_sources(self) -> Check:
        check = Check("sources")
        limit = self.config.health.max_source_failures
        failing = []
        for source in self.db.sources.list(enabled_only=True):
            state = self.db.fetch_state.get(source.id)
            if state.consecutive_failures >= limit:
                failing.append((source, state))
        check.details = {"failing": [s.name for s, _ in failing]}
        for source, state in failing:
            check.issues.append(
                (WARNING, f"{source.name}: {state.consecutive_failures} неудач подряд"
                          f" ({(state.last_error or '')[:80]})")
            )
        return check

    def check_processing(self) -> Check:
        check = Check("processing")
        limits = self.config.health
        unprocessed = self.db.documents.count_unprocessed()
        latest = self.db.processing_runs.latest()
        check.details = {
            "unprocessed": unprocessed,
            "last_run_status": latest.status if latest else None,
            "last_run_at": latest.finished_at if latest else None,
            "degraded": latest.degraded if latest else 0,
            "failed": latest.failed if latest else 0,
        }
        if unprocessed > limits.max_unprocessed:
            check.issues.append(
                (WARNING, f"документов без карточки: {unprocessed} (порог {limits.max_unprocessed})")
            )
        if latest is None:
            if unprocessed:
                check.issues.append((WARNING, "обработка ещё ни разу не запускалась"))
            return check
        if latest.status == "failed":
            check.issues.append((CRITICAL, f"последний прогон упал: {latest.error[:120]}"))
        elif latest.status == "done" and latest.processed:
            if latest.degraded == latest.processed:
                check.issues.append(
                    (CRITICAL, f"модель недоступна: все {latest.degraded} карточек собраны без неё")
                )
            elif latest.degraded:
                check.issues.append(
                    (WARNING, f"деградировавших карточек в последнем прогоне: {latest.degraded}")
                )
            if latest.failed:
                check.issues.append((WARNING, f"не обработано документов: {latest.failed}"))
        return check

    def check_delivery(self, now) -> Check:
        check = Check("delivery")
        last = self.db.deliveries.last()
        if last is None:
            check.issues.append((WARNING, "дайджест ещё ни разу не отправлялся"))
            return check
        sent = parse_datetime(last.sent_at)
        age = int((now - sent).total_seconds() // 60) if sent else None
        check.details = {"last_delivery_at": last.sent_at, "age_minutes": age, "chat": last.chat_id}
        limit = self.config.health.max_collect_age_minutes * 2
        if age is None or age > limit:
            check.issues.append((WARNING, f"последний дайджест ушёл {age} мин назад (порог {limit})"))
        return check

    # ── alert ──────────────────────────────────────────────────────────────

    @staticmethod
    def build_alert(report: Report) -> str:
        lines = ["ALERT: ⚠️ news-digest — проблемы конвейера", ""]
        for check in report.checks:
            if check.status == "healthy":
                continue
            icon = "❌" if check.status == CRITICAL else "⚠️"
            lines.append(f"ALERT: {icon} {LABELS.get(check.name, check.name)}: {check.status}")
            for severity, message in check.issues:
                tag = "КРИТ" if severity == CRITICAL else "ПРЕД"
                lines.append(f"ALERT:   [{tag}] {message}")
            lines.append("")
        lines.append(f"— проверено {report.checked_at[:16].replace('T', ' ')} UTC")
        return "\n".join(lines)

    def run(self, hash_path: str) -> tuple[int, Report]:
        """0 — здорово (тишина), 1 — алерт напечатан (и отправлен, если включено)."""
        report = self.run_checks()
        if report.overall == "healthy":
            return 0, report
        alert = self.build_alert(report)
        print(alert)
        if self.config.telegram.alerts:
            try:
                self.bot.send_alert(alert, hash_path)
            except DeliveryError as e:
                log.error("алерт не ушёл в Telegram: %s", e)
        return 1, report

