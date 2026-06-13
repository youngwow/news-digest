#!/usr/bin/env python3
"""Stats for the classifier-distillation dataset at data/training/dataset.jsonl.

Tracks progress toward the ~5k rows needed to fine-tune a local category
classifier (see CLAUDE.md "Training-data log").
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

from training_log import DATASET_PATH

TRAINING_TARGET_ROWS = 5000


def load_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def compute_stats(rows: list[dict]) -> dict:
    """Aggregate dataset stats; assumes a non-empty row list."""
    categories = Counter(r.get("category", "?") for r in rows)
    importances = [r["importance"] for r in rows
                   if isinstance(r.get("importance"), (int, float))]
    logged = sorted(r["logged_at"] for r in rows if r.get("logged_at"))
    return {
        "rows": len(rows),
        "unique_urls": len({r.get("url") for r in rows if r.get("url")}),
        "categories": categories,
        "importance_min": min(importances) if importances else None,
        "importance_avg": round(sum(importances) / len(importances), 1) if importances else None,
        "importance_max": max(importances) if importances else None,
        "first_logged": logged[0] if logged else None,
        "last_logged": logged[-1] if logged else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Training-dataset statistics")
    parser.add_argument("--path", default=DATASET_PATH,
                        help=f"Dataset path (default: {DATASET_PATH})")
    args = parser.parse_args()

    rows = load_rows(args.path)
    if not rows:
        print(f"(no training rows yet at {args.path})")
        return 0

    s = compute_stats(rows)
    pct = 100.0 * s["rows"] / TRAINING_TARGET_ROWS

    print(f"Dataset: {args.path}")
    print(f"Rows: {s['rows']} ({s['unique_urls']} unique URLs) — "
          f"{pct:.1f}% of the {TRAINING_TARGET_ROWS}-row training target")
    if s["first_logged"]:
        print(f"Collected: {s['first_logged'][:19]} … {s['last_logged'][:19]}")
    print(f"Importance: min {s['importance_min']} / avg {s['importance_avg']} / max {s['importance_max']}")
    print()
    print("Category distribution:")
    width = max(len(c) for c in s["categories"])
    for cat, n in s["categories"].most_common():
        share = 100.0 * n / s["rows"]
        print(f"  {cat:<{width}}  {n:>5}  {share:5.1f}%  {'█' * int(share / 2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
