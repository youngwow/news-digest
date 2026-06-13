"""Tests for health_check.py — especially the shape of `issues` entries.

Regression: error paths used to append plain strings to `issues`, while the
pretty-printer and pipeline_check.sh unpack (severity, msg) tuples — so the
monitor crashed exactly when a pipeline file was missing.
"""

import json
import os

import pytest

import health_check
from health_check import (
    check_classified,
    check_digest_json,
    check_digest_md,
    check_pipeline_status,
    check_raw_news,
    check_source_metrics,
    derive_status,
)


def _assert_issue_shape(result: dict) -> None:
    """Every issue must be a (severity, msg) pair the printers can unpack."""
    for issue in result["issues"]:
        severity, msg = issue  # raises if not a 2-item sequence
        assert severity in ("critical", "warning")
        assert isinstance(msg, str)


# ── missing-file paths (the old crash) ─────────────────────────────

def test_raw_news_missing_file_yields_tuple_issues(tmp_path):
    result = check_raw_news(str(tmp_path / "nope.json"))
    assert result["status"] == "critical"
    assert result["issues"]
    _assert_issue_shape(result)


def test_classified_missing_file_yields_tuple_issues(tmp_path):
    result = check_classified(str(tmp_path / "nope.json"), max_age=60)
    assert result["status"] == "critical"
    _assert_issue_shape(result)


def test_digest_json_missing_file_yields_tuple_issues(tmp_path):
    result = check_digest_json(str(tmp_path / "nope.json"))
    assert result["status"] == "critical"
    _assert_issue_shape(result)


def test_digest_md_missing_file_yields_tuple_issues(tmp_path):
    result = check_digest_md(str(tmp_path / "nope.md"))
    assert result["status"] == "critical"
    _assert_issue_shape(result)


def test_invalid_json_yields_tuple_issues(tmp_path):
    bad = tmp_path / "raw_news.json"
    bad.write_text("{not json", encoding="utf-8")
    result = check_raw_news(str(bad))
    assert result["status"] == "critical"
    _assert_issue_shape(result)


def test_source_metrics_missing_is_healthy(tmp_path):
    result = check_source_metrics(str(tmp_path / "nope.json"))
    assert result["status"] == "healthy"
    _assert_issue_shape(result)


# ── pipeline_status: skipped steps are not re-flagged ──────────────

@pytest.fixture
def status_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(health_check, "DATA_DIR", str(tmp_path))
    return tmp_path


def _write_status(dirpath, payload: dict) -> None:
    with open(os.path.join(dirpath, "pipeline_status.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_pipeline_status_missing_file_yields_tuple_issues(status_dir):
    result = check_pipeline_status()
    assert result["status"] == "warning"
    _assert_issue_shape(result)


def test_pipeline_status_flags_failed_but_not_skipped(status_dir):
    _write_status(status_dir, {
        "overall": "failed",
        "total_steps": 3,
        "failed_count": 1,
        "failed_steps": ["scrape"],
        "steps": [
            {"step": "scrape", "exit_code": 1, "status": "failed", "description": "x"},
            {"step": "classify", "exit_code": -1, "status": "skipped", "description": "y"},
            {"step": "merge", "exit_code": -1, "status": "skipped", "description": "z"},
        ],
    })
    result = check_pipeline_status()
    assert result["status"] == "critical"
    _assert_issue_shape(result)
    flagged = [msg for _, msg in result["issues"]]
    assert len(flagged) == 1
    assert "scrape" in flagged[0]


def test_pipeline_status_all_ok(status_dir):
    _write_status(status_dir, {
        "overall": "ok",
        "total_steps": 1,
        "failed_count": 0,
        "failed_steps": [],
        "steps": [{"step": "scrape", "exit_code": 0, "status": "ok", "description": "x"}],
    })
    result = check_pipeline_status()
    assert result["status"] == "healthy"
    assert result["details"]["all_steps_passed"] is True


# ── derive_status ───────────────────────────────────────────────────

def test_derive_status_empty_is_healthy():
    assert derive_status([]) == "healthy"


def test_derive_status_warning_only():
    assert derive_status([("warning", "x")]) == "warning"


def test_derive_status_critical_wins():
    assert derive_status([("warning", "x"), ("critical", "y")]) == "critical"
