"""DatasetStats: progress report over the distillation dataset (dataset.jsonl)."""

from __future__ import annotations

import json
import os
from collections import Counter

TRAINING_TARGET_ROWS = 5000


class DatasetStats:
    def __init__(self, dataset_path: str):
        self.dataset_path = dataset_path

    def load_rows(self) -> list[dict]:
        if not os.path.exists(self.dataset_path):
            return []
        rows: list[dict] = []
        with open(self.dataset_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return rows

    @staticmethod
    def compute(rows: list[dict]) -> dict:
        """Aggregate stats; assumes a non-empty row list."""
        importances = [r["importance"] for r in rows
                       if isinstance(r.get("importance"), (int, float))]
        logged = sorted(r["logged_at"] for r in rows if r.get("logged_at"))
        return {
            "rows": len(rows),
            "unique_urls": len({r.get("url") for r in rows if r.get("url")}),
            "categories": Counter(r.get("category", "?") for r in rows),
            "importance_min": min(importances) if importances else None,
            "importance_avg": round(sum(importances) / len(importances), 1) if importances else None,
            "importance_max": max(importances) if importances else None,
            "first_logged": logged[0] if logged else None,
            "last_logged": logged[-1] if logged else None,
        }

    def format_report(self) -> str:
        rows = self.load_rows()
        if not rows:
            return f"(no training rows yet at {self.dataset_path})"
        s = self.compute(rows)
        pct = 100.0 * s["rows"] / TRAINING_TARGET_ROWS
        lines = [
            f"Dataset: {self.dataset_path}",
            f"Rows: {s['rows']} ({s['unique_urls']} unique URLs) — "
            f"{pct:.1f}% of the {TRAINING_TARGET_ROWS}-row training target",
        ]
        if s["first_logged"]:
            lines.append(f"Collected: {s['first_logged'][:19]} … {s['last_logged'][:19]}")
        lines.append(f"Importance: min {s['importance_min']} / avg {s['importance_avg']} "
                     f"/ max {s['importance_max']}")
        lines.append("")
        lines.append("Category distribution:")
        width = max(len(c) for c in s["categories"])
        for cat, n in s["categories"].most_common():
            share = 100.0 * n / s["rows"]
            lines.append(f"  {cat:<{width}}  {n:>5}  {share:5.1f}%  {'█' * int(share / 2)}")
        return "\n".join(lines)
