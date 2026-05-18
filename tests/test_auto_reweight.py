"""Tests for auto_reweight: weight formula + dry-run/apply roundtrip."""

import json

import pytest

from auto_reweight import (
    MIN_SAMPLES_DEFAULT,
    WEIGHT_CEIL,
    WEIGHT_FLOOR,
    apply_reweight,
    compute_weight,
)

# ── compute_weight ────────────────────────────────────────────────

def _entry(success_rate=1.0, fetches=10):
    return {"fetches_total": fetches, "success_rate_ema": success_rate}


def test_compute_weight_full_success_yields_ceiling():
    assert compute_weight(_entry(success_rate=1.0)) == WEIGHT_CEIL


def test_compute_weight_complete_failure_yields_floor():
    assert compute_weight(_entry(success_rate=0.0)) == WEIGHT_FLOOR


def test_compute_weight_middle_values():
    # 0.5 + 1.5 * 0.7 = 1.55
    assert compute_weight(_entry(success_rate=0.7)) == pytest.approx(1.55, abs=0.01)
    # 0.5 + 1.5 * 0.5 = 1.25
    assert compute_weight(_entry(success_rate=0.5)) == pytest.approx(1.25, abs=0.01)


def test_compute_weight_skips_low_sample_count():
    assert compute_weight(_entry(success_rate=1.0, fetches=MIN_SAMPLES_DEFAULT - 1)) is None


def test_compute_weight_handles_out_of_range_rate():
    # success_rate >1 shouldn't push weight past ceiling
    assert compute_weight(_entry(success_rate=2.0)) == WEIGHT_CEIL
    # negative shouldn't push past floor
    assert compute_weight(_entry(success_rate=-0.5)) == WEIGHT_FLOOR


# ── apply_reweight roundtrip ──────────────────────────────────────

def _write_sources(path, sources):
    path.write_text(json.dumps({"sources": sources}, ensure_ascii=False, indent=2))


def _write_metrics(path, metrics):
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2))


def test_apply_reweight_dry_run_does_not_mutate_file(tmp_path):
    sp = tmp_path / "sources.json"
    mp = tmp_path / "source_metrics.json"
    _write_sources(sp, [{"name": "Meduza", "weight": 1.0}])
    _write_metrics(mp, {"Meduza": {"fetches_total": 10, "success_rate_ema": 1.0}})

    original = sp.read_text()
    changes = apply_reweight(str(sp), str(mp), apply=False)
    assert changes == [("Meduza", 1.0, 2.0)]
    assert sp.read_text() == original


def test_apply_reweight_writes_when_apply_true(tmp_path):
    sp = tmp_path / "sources.json"
    mp = tmp_path / "source_metrics.json"
    _write_sources(sp, [
        {"name": "Meduza", "weight": 1.0},
        {"name": "BBC", "weight": 1.0},
    ])
    _write_metrics(mp, {
        "Meduza": {"fetches_total": 10, "success_rate_ema": 1.0},
        "BBC":    {"fetches_total": 10, "success_rate_ema": 0.0},
    })

    changes = apply_reweight(str(sp), str(mp), apply=True)
    assert {(n, new) for n, _, new in changes} == {("Meduza", 2.0), ("BBC", 0.5)}

    saved = json.loads(sp.read_text())
    weights = {s["name"]: s["weight"] for s in saved["sources"]}
    assert weights == {"Meduza": 2.0, "BBC": 0.5}


def test_apply_reweight_skips_sources_without_metrics(tmp_path):
    sp = tmp_path / "sources.json"
    mp = tmp_path / "source_metrics.json"
    _write_sources(sp, [
        {"name": "Meduza", "weight": 1.0},
        {"name": "Untracked", "weight": 1.0},
    ])
    _write_metrics(mp, {"Meduza": {"fetches_total": 10, "success_rate_ema": 1.0}})

    changes = apply_reweight(str(sp), str(mp), apply=True)
    assert [n for n, _, _ in changes] == ["Meduza"]
    saved = json.loads(sp.read_text())
    # Untracked source weight preserved untouched
    untracked = next(s for s in saved["sources"] if s["name"] == "Untracked")
    assert untracked["weight"] == 1.0


def test_apply_reweight_skips_when_change_below_threshold(tmp_path):
    sp = tmp_path / "sources.json"
    mp = tmp_path / "source_metrics.json"
    # weight already very close to formula output for success_rate 1.0
    _write_sources(sp, [{"name": "Meduza", "weight": 2.0}])
    _write_metrics(mp, {"Meduza": {"fetches_total": 10, "success_rate_ema": 1.0}})

    changes = apply_reweight(str(sp), str(mp), apply=True)
    assert changes == []


def test_apply_reweight_missing_metrics_is_noop(tmp_path):
    sp = tmp_path / "sources.json"
    _write_sources(sp, [{"name": "Meduza", "weight": 1.0}])
    # no metrics file
    changes = apply_reweight(str(sp), str(tmp_path / "missing.json"), apply=True)
    assert changes == []


def test_apply_reweight_missing_sources_returns_empty(tmp_path, caplog):
    changes = apply_reweight(str(tmp_path / "nope.json"),
                              str(tmp_path / "also-nope.json"), apply=False)
    assert changes == []
