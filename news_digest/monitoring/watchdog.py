"""Watchdog: run health checks, emit a Russian ALERT block, push to Telegram.

Replaces pipeline_check.sh's body. Silent (exit 0) when healthy so a cron job
delivers nothing; otherwise prints the ALERT text (cron MAILTO / grep ALERT)
and pushes it via TelegramNotifier (no-op unless telegram.alerts).
"""

from __future__ import annotations

from ..config import Config
from ..delivery.telegram import TelegramNotifier
from ..jsonio import get_logger
from ..paths import ProjectPaths
from .health import CHECK_LABELS, HealthChecker

log = get_logger("watchdog")


class Watchdog:
    def __init__(self, config: Config, paths: ProjectPaths,
                 checker: HealthChecker | None = None,
                 notifier: TelegramNotifier | None = None):
        self.config = config
        self.paths = paths
        self.checker = checker or HealthChecker(config, paths)
        self.notifier = notifier or TelegramNotifier(config, paths)

    def build_alert(self, report: dict) -> str:
        lines = ["ALERT: ⚠️ Дайджест новостей — проблемы", ""]
        for name, check in report["checks"].items():
            if check["status"] == "healthy":
                continue
            desc = CHECK_LABELS.get(name, (name, name))[1]
            icon = "❌" if check["status"] == "critical" else "⚠️"
            lines.append(f"ALERT: {icon} {desc}: {check['status']}")
            for severity, msg in check.get("issues", []):
                tag = "КРИТ" if severity == "critical" else "ПРЕД"
                lines.append(f"ALERT:   [{tag}] {msg}")
            lines.append("")
        lines.append("—")
        lines.append(f"Детали: {self.paths.data('health.json')}")
        return "\n".join(lines)

    def run(self) -> int:
        """Return 0 when healthy (no output), 1 otherwise (alert printed + pushed)."""
        report = self.checker.run()
        if report["overall"] == "healthy":
            return 0
        alert = self.build_alert(report)
        print(alert)
        self.notifier.send_alert(alert)
        return 1
