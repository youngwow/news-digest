"""Tests for EMA update logic in scraper.py."""

import scraper
from scraper import _ema, update_source_metrics


def test_ema_seeds_from_first_sample():
    assert _ema(None, 100.0, alpha=0.3) == 100.0


def test_ema_converges_toward_target():
    """Repeated 0.0 samples should drag a 1.0 baseline toward 0."""
    val = 1.0
    for _ in range(50):
        val = _ema(val, 0.0, alpha=0.3)
    assert val < 0.001


def test_ema_weights_match_alpha():
    # 0.3 * 0 + 0.7 * 1 = 0.7
    assert abs(_ema(1.0, 0.0, alpha=0.3) - 0.7) < 1e-9


def test_update_source_metrics_creates_new_entry(monkeypatch, tmp_path):
    metrics_path = tmp_path / "source_metrics.json"
    monkeypatch.setattr(scraper, "METRICS_FILE", str(metrics_path))

    update_source_metrics([
        {"name": "Meduza", "success": True, "latency_ms": 400.0, "article_count": 30},
    ])
    import json
    saved = json.loads(metrics_path.read_text())
    assert saved["Meduza"]["fetches_total"] == 1
    assert saved["Meduza"]["successes_total"] == 1
    assert saved["Meduza"]["success_rate_ema"] == 1.0
    assert saved["Meduza"]["avg_latency_ms_ema"] == 400.0
    assert saved["Meduza"]["avg_articles_ema"] == 30
    assert "last_success" in saved["Meduza"]


def test_update_source_metrics_accumulates_across_runs(monkeypatch, tmp_path):
    metrics_path = tmp_path / "source_metrics.json"
    monkeypatch.setattr(scraper, "METRICS_FILE", str(metrics_path))

    update_source_metrics([
        {"name": "BBC", "success": True, "latency_ms": 200.0, "article_count": 20},
    ])
    update_source_metrics([
        {"name": "BBC", "success": False, "latency_ms": 30000.0, "article_count": 0},
    ])
    import json
    saved = json.loads(metrics_path.read_text())
    assert saved["BBC"]["fetches_total"] == 2
    assert saved["BBC"]["successes_total"] == 1
    # EMA: 0.3*0 + 0.7*1.0 = 0.7
    assert abs(saved["BBC"]["success_rate_ema"] - 0.7) < 1e-9
    assert "last_failure" in saved["BBC"]


def test_failure_does_not_update_avg_articles(monkeypatch, tmp_path):
    metrics_path = tmp_path / "source_metrics.json"
    monkeypatch.setattr(scraper, "METRICS_FILE", str(metrics_path))

    update_source_metrics([
        {"name": "DW", "success": True, "latency_ms": 500.0, "article_count": 50},
    ])
    update_source_metrics([
        {"name": "DW", "success": False, "latency_ms": 1000.0, "article_count": 0},
    ])
    import json
    saved = json.loads(metrics_path.read_text())
    # avg_articles_ema should still be 50 (failure didn't contribute)
    assert saved["DW"]["avg_articles_ema"] == 50
