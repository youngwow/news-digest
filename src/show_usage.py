#!/usr/bin/env python3
"""Tabular view of LLM token usage from data/llm_usage.jsonl."""

from __future__ import annotations

import argparse
import json
import os
import sys

from utils import DATA_DIR

USAGE_FILE = os.path.join(DATA_DIR, "llm_usage.jsonl")


def _load_records(path: str, limit: int) -> list[dict]:
    if not os.path.exists(path):
        return []
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
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
    parser = argparse.ArgumentParser(description="Show LLM usage per classify run")
    parser.add_argument("--limit", type=int, default=20,
                        help="Show last N runs (default 20; 0 = all)")
    args = parser.parse_args()

    records = _load_records(USAGE_FILE, args.limit)
    if not records:
        print(f"(no usage records yet at {USAGE_FILE})")
        return 0

    fmt = "{:<20}  {:<28}  {:>6}  {:>5}  {:>5}  {:>9}  {:>11}"
    print(fmt.format("run_at", "model", "chunks", "cache", "calls", "prompt_tk", "complete_tk"))
    print(fmt.format(*("-" * w for w in (20, 28, 6, 5, 5, 9, 11))))

    total_prompt = total_completion = 0
    for r in records:
        prompt_tk = r.get("prompt_tokens", 0)
        completion_tk = r.get("completion_tokens", 0)
        total_prompt += prompt_tk
        total_completion += completion_tk
        print(fmt.format(r.get("run_at", "?"), r.get("model", "?")[:28],
                         r.get("chunks_total", "?"), r.get("cache_hits", "?"),
                         r.get("api_calls", "?"), prompt_tk, completion_tk))

    print()
    print(f"Σ over {len(records)} run(s): {total_prompt} prompt + "
          f"{total_completion} completion = {total_prompt + total_completion} tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
