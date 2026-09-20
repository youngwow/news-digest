"""`run` — один запуск конвейера для cron: сбор → обработка → дайджест → доставка.

Шаги гейтятся: упавший сбор не запускает обработку на старых данных, упавшая
обработка не шлёт дайджест; деградация (модель недоступна, карточки собраны
экстрактивно) дайджест не останавливает — лента не должна пустеть, а сторож об
этом скажет. Атомарный lock (`mkdir data/.run.lock`) делает пересекающиеся
запуски no-op; забытый lock старше `STALE_LOCK_HOURS` снимается с предупреждением.
Итог каждого шага пишется в `data/pipeline_status.json` — для внешнего
мониторинга; история сборов, прогонов и доставок и так лежит в базе.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from ..config import Config
from ..paths import ProjectPaths
from ..utils import get_logger

log = get_logger("run")

STALE_LOCK_HOURS = 6


class RunLock:
    """Atomic mkdir-based mutex so overlapping runs (e.g. cron) no-op cleanly."""

    def __init__(self, path: str, stale_hours: float = STALE_LOCK_HOURS):
        self.path = path
        self.stale_seconds = stale_hours * 3600

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        try:
            os.mkdir(self.path)
            return True
        except FileExistsError:
            pass
        try:
            age = time.time() - os.stat(self.path).st_mtime
        except OSError:
            return False
        if age <= self.stale_seconds:
            return False
        log.warning("lock %s старше %.0f ч — снимаю как забытый", self.path, age / 3600)
        self.release()
        try:
            os.mkdir(self.path)
            return True
        except FileExistsError:
            return False

    def release(self) -> None:
        try:
            os.rmdir(self.path)
        except OSError:
            pass


@dataclass
class StepResult:
    name: str
    status: str  # ok | degraded | failed | skipped | empty
    started: str
    finished: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "step": self.name,
            "status": self.status,
            "started": self.started,
            "finished": self.finished,
            "detail": self.detail,
        }


@dataclass
class RunResult:
    steps: list[StepResult] = field(default_factory=list)
    locked: bool = False

    @property
    def exit_code(self) -> int:
        if self.locked:
            return 0
        statuses = [s.status for s in self.steps]
        if "failed" in statuses:
            return 1
        return 2 if "degraded" in statuses else 0

    def to_dict(self) -> dict:
        return {"exit_code": self.exit_code, "locked": self.locked, "steps": [s.to_dict() for s in self.steps]}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class PipelineRun:
    """Собирает шаги из готовых сервисов; каждый шаг — callable, возвращающий (status, detail)."""

    def __init__(self, config: Config, paths: ProjectPaths):
        self.config = config
        self.paths = paths
        self.lock = RunLock(paths.data(".run.lock"))
        self.status_path = paths.data("pipeline_status.json")

    def run(
        self,
        *,
        collect: Callable[[], tuple[str, str]],
        process: Callable[[], tuple[str, str]],
        digest: Callable[[], tuple[str, str]],
        do_collect: bool = True,
        do_process: bool = True,
        do_digest: bool = True,
    ) -> RunResult:
        result = RunResult()
        if not self.lock.acquire():
            log.warning("другой запуск ещё идёт (%s) — выхожу", self.lock.path)
            result.locked = True
            return result
        try:
            gate_open = True
            for name, step, enabled in (
                ("collect", collect, do_collect),
                ("process", process, do_process),
                ("digest", digest, do_digest),
            ):
                started = _now()
                if not enabled:
                    result.steps.append(StepResult(name, "skipped", started, started, "выключен флагом"))
                    continue
                if not gate_open:
                    result.steps.append(
                        StepResult(name, "skipped", started, started, "предыдущий шаг упал")
                    )
                    continue
                try:
                    status, detail = step()
                except Exception as e:  # noqa: BLE001 — шаг упал, остальное гейтится
                    log.exception("шаг %s упал", name)
                    status, detail = "failed", f"{type(e).__name__}: {e}"
                result.steps.append(StepResult(name, status, started, _now(), detail))
                log.info("шаг %s: %s%s", name, status, f" — {detail}" if detail else "")
                if status == "failed":
                    gate_open = False
        finally:
            self.lock.release()
            self._write_status(result)
        return result

    def _write_status(self, result: RunResult) -> None:
        try:
            os.makedirs(os.path.dirname(self.status_path) or ".", exist_ok=True)
            tmp = f"{self.status_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"finished_at": _now(), **result.to_dict()}, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, self.status_path)
        except OSError as e:
            log.warning("не удалось записать %s: %s", self.status_path, e)
