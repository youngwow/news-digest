"""Tests for Pipeline orchestration — gating, lock, status/history writing."""

import json

from support import valid_raw_config

from news_digest.config import Config
from news_digest.paths import ProjectPaths
from news_digest.pipeline import Pipeline, RunLock


def _pipeline(tmp_path):
    (tmp_path / "data").mkdir(exist_ok=True)
    cfg = Config.from_dict(valid_raw_config())
    return Pipeline(cfg, ProjectPaths.from_root(str(tmp_path)))


def _stub(p, failing: set[str]):
    """Replace every _phase_* with a stub; names in `failing` return exit 1."""
    phases = ["scrape", "extract", "split", "classify", "merge",
              "assemble", "format", "deliver", "cleanup"]
    for name in phases:
        setattr(p, f"_phase_{name}",
                (lambda n=name: 1 if n in failing else 0))


def _status(tmp_path):
    return json.loads((tmp_path / "data" / "pipeline_status.json").read_text())


# ── RunLock ─────────────────────────────────────────────────────────

def test_runlock_blocks_second_acquire(tmp_path):
    lock = RunLock(str(tmp_path / "data" / ".run.lock"))
    assert lock.acquire() is True
    assert RunLock(str(tmp_path / "data" / ".run.lock")).acquire() is False
    lock.release()
    assert RunLock(str(tmp_path / "data" / ".run.lock")).acquire() is True


def test_lock_conflict_is_noop(tmp_path):
    p = _pipeline(tmp_path)
    _stub(p, failing=set())
    assert p.lock.acquire() is True  # hold the lock
    try:
        assert p.run() == 0  # second run no-ops
        assert not (tmp_path / "data" / "pipeline_status.json").exists()
    finally:
        p.lock.release()


# ── happy path ──────────────────────────────────────────────────────

def test_clean_run_all_steps_ok(tmp_path):
    p = _pipeline(tmp_path)
    _stub(p, failing=set())
    assert p.run() == 0
    status = _status(tmp_path)
    assert status["overall"] == "ok"
    names = [s["step"] for s in status["steps"]]
    assert names == ["scrape", "extract", "split", "classify", "merge",
                     "assemble", "format", "deliver", "cleanup"]
    assert all(s["status"] == "ok" for s in status["steps"])


# ── gating ──────────────────────────────────────────────────────────

def test_scrape_failure_skips_downstream(tmp_path):
    p = _pipeline(tmp_path)
    _stub(p, failing={"scrape"})
    assert p.run() == 1
    status = _status(tmp_path)
    assert status["overall"] == "failed"
    by_name = {s["step"]: s["status"] for s in status["steps"]}
    assert by_name["scrape"] == "failed"
    for name in ["extract", "split", "classify", "merge", "assemble", "format", "deliver"]:
        assert by_name[name] == "skipped"
    assert "cleanup" not in by_name  # cleanup not recorded on a failed run


def test_classify_failure_still_runs_merge_but_skips_deliver(tmp_path):
    p = _pipeline(tmp_path)
    _stub(p, failing={"classify"})
    assert p.run() == 1
    by_name = {s["step"]: s["status"] for s in _status(tmp_path)["steps"]}
    assert by_name["classify"] == "failed"
    assert by_name["merge"] == "ok"        # downstream siblings still run
    assert by_name["assemble"] == "ok"
    assert by_name["deliver"] == "skipped"  # deliver gated on a fully-clean run
    assert "cleanup" not in by_name


def test_resume_skips_upstream(tmp_path):
    (tmp_path / "data" / "chunks").mkdir(parents=True)
    (tmp_path / "data" / "chunks" / "chunk_1.json").write_text("{}", encoding="utf-8")
    p = _pipeline(tmp_path)
    _stub(p, failing=set())
    assert p.run(resume=True) == 0
    names = [s["step"] for s in _status(tmp_path)["steps"]]
    assert "scrape" not in names and "classify" in names


def test_history_appended(tmp_path):
    p = _pipeline(tmp_path)
    _stub(p, failing=set())
    p.run()
    hist = (tmp_path / "data" / "pipeline_history.jsonl").read_text().strip()
    assert json.loads(hist)["overall"] == "ok"


def test_alerts_log_on_failure(tmp_path):
    p = _pipeline(tmp_path)
    _stub(p, failing={"scrape"})
    p.run()
    alerts = (tmp_path / "data" / "alerts.log").read_text()
    assert "FAIL" in alerts and "scrape" in alerts
