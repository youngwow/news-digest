"""Tests for HealthChecker — issue-shape invariant, skipped-step handling, watchdog."""

import json

import pytest
from support import valid_raw_config

from news_digest.config import Config
from news_digest.monitoring.health import HealthChecker, derive_status
from news_digest.monitoring.watchdog import Watchdog
from news_digest.paths import ProjectPaths


@pytest.fixture
def checker(tmp_path):
    (tmp_path / "data").mkdir(exist_ok=True)
    cfg = Config.from_dict(valid_raw_config())
    return HealthChecker(cfg, ProjectPaths.from_root(str(tmp_path)))


def _assert_tuples(result):
    for issue in result["issues"]:
        sev, msg = issue
        assert sev in ("critical", "warning")
        assert isinstance(msg, str)


def test_raw_news_missing_yields_tuple_issues(checker, tmp_path):
    result = checker.check_raw_news(str(tmp_path / "nope.json"))
    assert result["status"] == "critical"
    _assert_tuples(result)


def test_classified_missing_yields_tuple_issues(checker, tmp_path):
    result = checker.check_classified(str(tmp_path / "nope.json"), max_age=60)
    assert result["status"] == "critical"
    _assert_tuples(result)


def test_digest_json_missing_yields_tuple_issues(checker, tmp_path):
    result = checker.check_digest_json(str(tmp_path / "nope.json"))
    assert result["status"] == "critical"
    _assert_tuples(result)


def test_digest_md_missing_yields_tuple_issues(checker, tmp_path):
    result = checker.check_digest_md(str(tmp_path / "nope.md"))
    assert result["status"] == "critical"
    _assert_tuples(result)


def test_invalid_json_yields_tuple_issues(checker, tmp_path):
    bad = tmp_path / "raw_news.json"
    bad.write_text("{not json", encoding="utf-8")
    result = checker.check_raw_news(str(bad))
    assert result["status"] == "critical"
    _assert_tuples(result)


def test_source_metrics_missing_is_healthy(checker, tmp_path):
    result = checker.check_source_metrics(str(tmp_path / "nope.json"))
    assert result["status"] == "healthy"


def _write_status(checker, payload):
    with open(checker.paths.data("pipeline_status.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_pipeline_status_missing_yields_tuple_issues(checker):
    result = checker.check_pipeline_status()
    assert result["status"] == "warning"
    _assert_tuples(result)


def test_pipeline_status_flags_failed_not_skipped(checker):
    _write_status(checker, {
        "overall": "failed", "total_steps": 3, "failed_count": 1, "failed_steps": ["scrape"],
        "steps": [
            {"step": "scrape", "exit_code": 1, "status": "failed", "description": "x"},
            {"step": "classify", "exit_code": -1, "status": "skipped", "description": "y"},
            {"step": "merge", "exit_code": -1, "status": "skipped", "description": "z"},
        ]})
    result = checker.check_pipeline_status()
    assert result["status"] == "critical"
    flagged = [msg for _, msg in result["issues"]]
    assert len(flagged) == 1 and "scrape" in flagged[0]


def test_pipeline_status_all_ok(checker):
    _write_status(checker, {
        "overall": "ok", "total_steps": 1, "failed_count": 0, "failed_steps": [],
        "steps": [{"step": "scrape", "exit_code": 0, "status": "ok", "description": "x"}]})
    result = checker.check_pipeline_status()
    assert result["status"] == "healthy"
    assert result["details"]["all_steps_passed"] is True


def test_run_writes_health_json(checker):
    report = checker.run()
    assert report["overall"] in ("healthy", "warning", "critical")
    assert json.load(open(checker.paths.data("health.json")))["overall"] == report["overall"]


# ── derive_status ───────────────────────────────────────────────────

def test_derive_status_levels():
    assert derive_status([]) == "healthy"
    assert derive_status([("warning", "x")]) == "warning"
    assert derive_status([("warning", "x"), ("critical", "y")]) == "critical"


# ── Watchdog ────────────────────────────────────────────────────────

class _FakeChecker:
    def __init__(self, report):
        self._report = report

    def run(self):
        return self._report


class _FakeNotifier:
    def __init__(self):
        self.alerts = []

    def send_alert(self, text):
        self.alerts.append(text)
        return 0


def _watchdog(tmp_path, report):
    cfg = Config.from_dict(valid_raw_config())
    paths = ProjectPaths.from_root(str(tmp_path))
    return Watchdog(cfg, paths, checker=_FakeChecker(report), notifier=_FakeNotifier())


def test_watchdog_silent_when_healthy(tmp_path, capsys):
    wd = _watchdog(tmp_path, {"overall": "healthy", "checks": {}})
    assert wd.run() == 0
    assert capsys.readouterr().out == ""
    assert wd.notifier.alerts == []


def test_watchdog_alerts_when_unhealthy(tmp_path, capsys):
    report = {"overall": "warning", "checks": {
        "source_metrics": {"status": "warning",
                            "issues": [("warning", "source 'X' rolling success rate is 0.0 (< 0.5)")]}}}
    wd = _watchdog(tmp_path, report)
    assert wd.run() == 1
    out = capsys.readouterr().out
    assert "ALERT:" in out and "Здоровье источников" in out
    assert len(wd.notifier.alerts) == 1
