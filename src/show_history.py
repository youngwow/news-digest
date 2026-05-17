#!/usr/bin/env python3
"""Tabular view of past pipeline runs from data/pipeline_history.jsonl."""

from __future__ import annotations

import argparse
import json
import os
import sys

from utils import DATA_DIR

HISTORY_FILE = os.path.join(DATA_DIR, "pipeline_history.jsonl")


def _load_records(limit: int) -> list[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    records: list[dict] = []
    with open(HISTORY_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit > 0:
        records = records[-limit:]
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Show pipeline run history")
    parser.add_argument("--limit", type=int, default=20,
                        help="Show last N runs (default 20; 0 = all)")
    args = parser.parse_args()

    records = _load_records(args.limit)
    if not records:
        print(f"(no history yet at {HISTORY_FILE})")
        return 0

    header = ("run_at", "overall", "steps", "failed", "duration_s", "failed_steps")
    widths = [24, 7, 5, 6, 10, 0]
    fmt = "{:<24}  {:<7}  {:>5}  {:>6}  {:>10}  {}"
    print(fmt.format(*header))
    print(fmt.format(*["-" * w if w else "-" * 12 for w in widths]))

    for r in records:
        run_at = r.get("run_at", "?")
        overall = r.get("overall", "?")
        total = r.get("total_steps", "?")
        failed = r.get("failed_count", 0)
        duration = r.get("duration_s")
        duration_str = f"{duration}" if duration is not None else "?"
        failed_steps = ",".join(r.get("failed_steps", [])) or "-"
        print(fmt.format(run_at, overall, total, failed, duration_str, failed_steps))

    return 0


if __name__ == "__main__":
    sys.exit(main())
