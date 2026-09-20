"""Tests for news_digest.sources.metrics.Reweighter — formula + dry-run/apply."""

import json

import pytest

from news_digest.sources.metrics import (
    MIN_SAMPLES_DEFAULT,
    WEIGHT_CEIL,
    WEIGHT_FLOOR,
    Reweighter,
    SourceMetrics,
)


def _entry(success_rate=1.0, fetches=10):
    return {"fetches_total": fetches, "success_rate_ema": success_rate}


def test_compute_weight_full_success_yields_ceiling():
    assert Reweighter.compute_weight(_entry(success_rate=1.0), MIN_SAMPLES_DEFAULT) == WEIGHT_CEIL


def test_compute_weight_complete_failure_yields_floor():
    assert Reweighter.compute_weight(_entry(success_rate=0.0), MIN_SAMPLES_DEFAULT) == WEIGHT_FLOOR


def test_compute_weight_middle_values():
    assert Reweighter.compute_weight(_entry(success_rate=0.7), MIN_SAMPLES_DEFAULT) == pytest.approx(1.55, abs=0.01)
    assert Reweighter.compute_weight(_entry(success_rate=0.5), MIN_SAMPLES_DEFAULT) == pytest.approx(1.25, abs=0.01)


def test_compute_weight_skips_low_sample_count():
    assert Reweighter.compute_weight(_entry(fetches=MIN_SAMPLES_DEFAULT - 1), MIN_SAMPLES_DEFAULT) is None


def test_compute_weight_handles_out_of_range_rate():
    assert Reweighter.compute_weight(_entry(success_rate=2.0), MIN_SAMPLES_DEFAULT) == WEIGHT_CEIL
    assert Reweighter.compute_weight(_entry(success_rate=-0.5), MIN_SAMPLES_DEFAULT) == WEIGHT_FLOOR


def _reweighter(tmp_path, sources, metrics):
    sp = tmp_path / "sources.json"
    mp = tmp_path / "source_metrics.json"
    sp.write_text(json.dumps({"sources": sources}, ensure_ascii=False))
    mp.write_text(json.dumps(metrics, ensure_ascii=False))
    return Reweighter(str(sp), SourceMetrics(str(mp))), sp


def test_dry_run_does_not_mutate_file(tmp_path):
    rw, sp = _reweighter(tmp_path, [{"name": "Meduza", "weight": 1.0}],
                         {"Meduza": {"fetches_total": 10, "success_rate_ema": 1.0}})
    original = sp.read_text()
    assert rw.propose(apply=False) == [("Meduza", 1.0, 2.0)]
    assert sp.read_text() == original


def test_apply_writes_changes(tmp_path):
    rw, sp = _reweighter(
        tmp_path,
        [{"name": "Meduza", "weight": 1.0}, {"name": "BBC", "weight": 1.0}],
        {"Meduza": {"fetches_total": 10, "success_rate_ema": 1.0},
         "BBC": {"fetches_total": 10, "success_rate_ema": 0.0}})
    changes = rw.propose(apply=True)
    assert {(n, new) for n, _, new in changes} == {("Meduza", 2.0), ("BBC", 0.5)}
    weights = {s["name"]: s["weight"] for s in json.loads(sp.read_text())["sources"]}
    assert weights == {"Meduza": 2.0, "BBC": 0.5}


def test_skips_sources_without_metrics(tmp_path):
    rw, sp = _reweighter(
        tmp_path,
        [{"name": "Meduza", "weight": 1.0}, {"name": "Untracked", "weight": 1.0}],
        {"Meduza": {"fetches_total": 10, "success_rate_ema": 1.0}})
    changes = rw.propose(apply=True)
    assert [n for n, _, _ in changes] == ["Meduza"]
    untracked = next(s for s in json.loads(sp.read_text())["sources"] if s["name"] == "Untracked")
    assert untracked["weight"] == 1.0


def test_skips_change_below_threshold(tmp_path):
    rw, _ = _reweighter(tmp_path, [{"name": "Meduza", "weight": 2.0}],
                        {"Meduza": {"fetches_total": 10, "success_rate_ema": 1.0}})
    assert rw.propose(apply=True) == []


def test_missing_sources_returns_empty(tmp_path):
    rw = Reweighter(str(tmp_path / "nope.json"), SourceMetrics(str(tmp_path / "m.json")))
    assert rw.propose(apply=False) == []
