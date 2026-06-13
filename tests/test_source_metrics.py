"""Tests for news_digest.sources.metrics — EMA and SourceMetrics.update."""

from news_digest.sources.metrics import SourceMetrics, ema


def test_ema_seeds_from_first_sample():
    assert ema(None, 100.0, alpha=0.3) == 100.0


def test_ema_converges_toward_target():
    val = 1.0
    for _ in range(50):
        val = ema(val, 0.0, alpha=0.3)
    assert val < 0.001


def test_ema_weights_match_alpha():
    assert abs(ema(1.0, 0.0, alpha=0.3) - 0.7) < 1e-9


def _metrics(tmp_path) -> SourceMetrics:
    return SourceMetrics(str(tmp_path / "source_metrics.json"))


def test_update_creates_new_entry(tmp_path):
    m = _metrics(tmp_path)
    m.update([{"name": "Meduza", "success": True, "latency_ms": 400.0, "article_count": 30}])
    saved = m.load()["Meduza"]
    assert saved["fetches_total"] == 1
    assert saved["successes_total"] == 1
    assert saved["success_rate_ema"] == 1.0
    assert saved["avg_latency_ms_ema"] == 400.0
    assert saved["avg_articles_ema"] == 30
    assert "last_success" in saved


def test_update_accumulates_across_runs(tmp_path):
    m = _metrics(tmp_path)
    m.update([{"name": "BBC", "success": True, "latency_ms": 200.0, "article_count": 20}])
    m.update([{"name": "BBC", "success": False, "latency_ms": 30000.0, "article_count": 0}])
    saved = m.load()["BBC"]
    assert saved["fetches_total"] == 2
    assert saved["successes_total"] == 1
    assert abs(saved["success_rate_ema"] - 0.7) < 1e-9
    assert "last_failure" in saved


def test_failure_does_not_update_avg_articles(tmp_path):
    m = _metrics(tmp_path)
    m.update([{"name": "DW", "success": True, "latency_ms": 500.0, "article_count": 50}])
    m.update([{"name": "DW", "success": False, "latency_ms": 1000.0, "article_count": 0}])
    assert m.load()["DW"]["avg_articles_ema"] == 50
