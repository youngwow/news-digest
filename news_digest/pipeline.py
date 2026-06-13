"""Pipeline orchestrator — replaces run.sh's step running, gating, and status I/O.

Step gating mirrors run.sh exactly:
  • scrape→extract→split are "upstream"; a failure there skips everything downstream.
  • classify/merge/assemble/format run whenever upstream is OK (independent of each other).
  • deliver and cleanup run only on a fully-clean run.
  • --resume skips scrape/extract/split entirely (re-using existing data/chunks/).
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .chunking import Chunker
from .classify.classifier import ChunkClassifier
from .config import Config
from .delivery.telegram import TelegramNotifier
from .digest.assembler import DigestAssembler
from .digest.merge import ChunkMerger
from .digest.render import TelegramRenderer, split_message
from .jsonio import get_logger, load_json, save_json
from .models import DigestDocument
from .monitoring.history import RunHistory
from .paths import ProjectPaths

log = get_logger("pipeline")


@dataclass
class Step:
    name: str
    description: str
    func: Callable[[], int]   # returns a process-style exit code (0 == success)
    upstream: bool = False    # part of the scrape/extract/split gating group


@dataclass
class StepResult:
    name: str
    exit_code: int
    description: str
    started: str
    finished: str

    @property
    def status(self) -> str:
        return "ok" if self.exit_code == 0 else "skipped" if self.exit_code == -1 else "failed"

    def to_dict(self) -> dict:
        return {"step": self.name, "exit_code": self.exit_code, "description": self.description,
                "started": self.started, "finished": self.finished, "status": self.status}


class RunLock:
    """Atomic mkdir-based mutex so overlapping runs (e.g. cron) no-op cleanly."""

    def __init__(self, path: str):
        self.path = path

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
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


class Pipeline:
    def __init__(self, config: Config, paths: ProjectPaths):
        self.config = config
        self.paths = paths
        self.lock = RunLock(paths.data(".run.lock"))
        self.history = RunHistory(paths.data("pipeline_history.jsonl"))

    # ── phase callables (each returns an exit code) ──────────────────

    def _phase_scrape(self) -> int:
        from .sources.scraper import Scraper
        Scraper(self.config, self.paths).run()
        return 0

    def _phase_extract(self) -> int:
        Chunker(self.config, self.paths).extract()
        return 0

    def _phase_split(self) -> int:
        Chunker(self.config, self.paths).split()
        return 0

    def _phase_classify(self) -> int:
        return 0 if ChunkClassifier(self.config, self.paths).classify_all() else 1

    def _phase_merge(self) -> int:
        ChunkMerger(self.config, self.paths).run()
        return 0

    def _phase_assemble(self) -> int:
        DigestAssembler(self.config, self.paths).assemble()
        return 0

    def _phase_format(self) -> int:
        path = self.paths.data("digest.json")
        if not os.path.exists(path):
            log.error("digest.json not found at %s", path)
            return 1
        doc = DigestDocument.from_dict(load_json(path))
        text = TelegramRenderer(self.config.categories).render(doc)
        parts = split_message(text, self.config.telegram.message_limit)
        if len(parts) > 1:
            log.info("digest split into %d parts", len(parts))
        for i, part in enumerate(parts, 1):
            if len(parts) > 1:
                print(f"\n[{i}/{len(parts)}]")
            print(part)
        return 0

    def _phase_deliver(self) -> int:
        return TelegramNotifier(self.config, self.paths).deliver()

    def _phase_cleanup(self) -> int:
        Chunker(self.config, self.paths).cleanup()
        return 0

    # ── execution ────────────────────────────────────────────────────

    @staticmethod
    def _now_local() -> str:
        return datetime.now().astimezone().isoformat()

    def _execute(self, step: Step) -> StepResult:
        log.info("=== [%s] %s ===", step.name, step.description)
        started = self._now_local()
        try:
            code = step.func()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        except Exception as e:  # noqa: BLE001 — a crashing phase is a failed step, not a traceback
            log.error("%s: unhandled error: %s", step.name, e)
            code = 1
        finished = self._now_local()
        log.info("  %s %s: %s", "✓" if code == 0 else "✗", step.name,
                 "OK" if code == 0 else f"FAILED (exit {code})")
        return StepResult(step.name, code, step.description, started, finished)

    def _skipped(self, step: Step) -> StepResult:
        log.info("=== [%s] %s — SKIPPED (upstream failure) ===", step.name, step.description)
        ts = self._now_local()
        return StepResult(step.name, -1, step.description, ts, ts)

    def run(self, resume: bool = False) -> int:
        if not self.lock.acquire():
            log.info("Another pipeline run holds the lock at %s — exiting (no-op)", self.lock.path)
            return 0
        start_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S MSK")
        try:
            return self._run_steps(resume, start_local)
        finally:
            self.lock.release()

    def _run_steps(self, resume: bool, start_local: str) -> int:
        upstream = [
            Step("scrape", "Scrape RSS/Atom feeds → raw_news.json", self._phase_scrape, True),
            Step("extract", "Extract flat article list → articles.json", self._phase_extract, True),
            Step("split", "Split articles into chunks for AI classification", self._phase_split, True),
        ]
        downstream = [
            Step("classify", "Classify chunks via LLM (parallel)", self._phase_classify),
            Step("merge", "Merge classified chunks + cross-chunk dedup → classified.json", self._phase_merge),
            Step("assemble", "Assemble digest.json + digest.md from classified stories", self._phase_assemble),
            Step("format", "Format Telegram output → stdout", self._phase_format),
        ]

        if resume and not glob.glob(self.paths.data("chunks", "chunk_*.json")):
            log.info("Resume requested but no data/chunks/ found — falling back to full run")
            resume = False

        results: list[StepResult] = []
        upstream_ok = True

        if not resume:
            for step in upstream:
                if upstream_ok:
                    r = self._execute(step)
                    if r.status == "failed":
                        upstream_ok = False
                else:
                    r = self._skipped(step)
                results.append(r)
            if not upstream_ok:
                log.info("=== upstream step failed — skipping classify→format ===")
        else:
            log.info("Resume mode: skipping scrape/extract/split (existing data/chunks/ preserved)")

        for step in downstream:
            results.append(self._execute(step) if upstream_ok else self._skipped(step))

        clean_so_far = all(r.status != "failed" for r in results)
        deliver = Step("deliver", "Send digest to Telegram (no-op if telegram.enabled=false)",
                       self._phase_deliver)
        results.append(self._execute(deliver) if clean_so_far else self._skipped(deliver))

        # cleanup only on a fully-clean run; otherwise data/chunks/ is preserved for --resume
        if all(r.status != "failed" for r in results):
            results.append(self._execute(
                Step("cleanup", "Remove temporary chunk files from chunks/", self._phase_cleanup)))
        else:
            log.info("=== [cleanup] skipped — failures present; data/chunks/ preserved ===")

        failed = [r for r in results if r.status == "failed"]
        self._write_status(results, failed, start_local)
        self._append_history(results, failed)

        if failed:
            self._append_alert(failed)
            log.warning("⚠️  %d step(s) failed: %s", len(failed), ", ".join(r.name for r in failed))
            return 1
        return 0

    def _write_status(self, results, failed, start_local) -> None:
        save_json(self.paths.data("pipeline_status.json"), {
            "pipeline_run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pipeline_start_local": start_local,
            "overall": "failed" if failed else "ok",
            "total_steps": len(results),
            "failed_count": len(failed),
            "failed_steps": [r.name for r in failed],
            "steps": [r.to_dict() for r in results],
        })

    def _append_history(self, results, failed) -> None:
        duration = None
        if results:
            try:
                start = datetime.fromisoformat(results[0].started)
                end = datetime.fromisoformat(results[-1].finished)
                duration = int((end - start).total_seconds())
            except ValueError:
                pass
        self.history.append({
            "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "overall": "failed" if failed else "ok",
            "total_steps": len(results),
            "failed_count": len(failed),
            "failed_steps": [r.name for r in failed],
            "duration_s": duration,
        })

    def _append_alert(self, failed) -> None:
        line = (f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} FAIL "
                f"{','.join(r.name for r in failed)} — {len(failed)} step(s) failed\n")
        try:
            with open(self.paths.data("alerts.log"), "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            log.warning("could not append to alerts.log: %s", e)
