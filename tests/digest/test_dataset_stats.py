"""Tests for DatasetStats."""

import json

from news_digest.training.stats import DatasetStats


def _rows():
    return [
        {"url": "http://a.com/1", "category": "политика", "importance": 8,
         "logged_at": "2026-06-10T21:05:14+00:00"},
        {"url": "http://b.com/2", "category": "политика", "importance": 6,
         "logged_at": "2026-06-11T00:32:36+00:00"},
        {"url": "http://a.com/1", "category": "спорт", "importance": 4,
         "logged_at": "2026-06-11T00:32:36+00:00"},
    ]


def _write(tmp_path, rows) -> str:
    path = tmp_path / "dataset.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return str(path)


def test_load_rows_skips_broken_lines(tmp_path):
    path = tmp_path / "dataset.jsonl"
    path.write_text('{"url": "http://a"}\n{broken\n\n{"url": "http://b"}\n', encoding="utf-8")
    assert len(DatasetStats(str(path)).load_rows()) == 2


def test_load_rows_missing_file():
    assert DatasetStats("/nonexistent/dataset.jsonl").load_rows() == []


def test_compute():
    s = DatasetStats.compute(_rows())
    assert s["rows"] == 3
    assert s["unique_urls"] == 2
    assert s["categories"]["политика"] == 2
    assert (s["importance_min"], s["importance_avg"], s["importance_max"]) == (4, 6.0, 8)
    assert s["first_logged"].startswith("2026-06-10")
    assert s["last_logged"].startswith("2026-06-11")


def test_format_report(tmp_path):
    report = DatasetStats(_write(tmp_path, _rows())).format_report()
    assert "Rows: 3" in report
    assert "политика" in report


def test_format_report_empty(tmp_path):
    assert "no training rows" in DatasetStats(str(tmp_path / "nope.jsonl")).format_report()
