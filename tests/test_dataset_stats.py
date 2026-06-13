"""Tests for dataset_stats.py."""

import json

from dataset_stats import compute_stats, load_rows


def _rows() -> list[dict]:
    return [
        {"url": "http://a.com/1", "category": "политика", "importance": 8,
         "logged_at": "2026-06-10T21:05:14+00:00"},
        {"url": "http://b.com/2", "category": "политика", "importance": 6,
         "logged_at": "2026-06-11T00:32:36+00:00"},
        {"url": "http://a.com/1", "category": "спорт", "importance": 4,
         "logged_at": "2026-06-11T00:32:36+00:00"},  # duplicate URL
    ]


def _write(tmp_path, rows) -> str:
    path = tmp_path / "dataset.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                    encoding="utf-8")
    return str(path)


def test_load_rows_skips_broken_lines(tmp_path):
    path = tmp_path / "dataset.jsonl"
    path.write_text('{"url": "http://a.com"}\n{broken\n\n{"url": "http://b.com"}\n',
                    encoding="utf-8")
    assert len(load_rows(str(path))) == 2


def test_load_rows_missing_file():
    assert load_rows("/nonexistent/dataset.jsonl") == []


def test_compute_stats():
    s = compute_stats(_rows())
    assert s["rows"] == 3
    assert s["unique_urls"] == 2
    assert s["categories"]["политика"] == 2
    assert s["categories"]["спорт"] == 1
    assert (s["importance_min"], s["importance_avg"], s["importance_max"]) == (4, 6.0, 8)
    assert s["first_logged"].startswith("2026-06-10")
    assert s["last_logged"].startswith("2026-06-11")


def test_roundtrip_through_file(tmp_path):
    rows = load_rows(_write(tmp_path, _rows()))
    assert compute_stats(rows)["rows"] == 3
