#!/usr/bin/env python3
"""
Compute new per-source weights in sources.json from rolling success rate.

Default mode is dry-run: prints a table of proposed changes without writing.
Pass --apply to update sources.json on disk.

    python3 src/auto_reweight.py               # dry-run
    python3 src/auto_reweight.py --apply       # write changes

Formula:  weight = clamp(0.5 + 1.5 * success_rate_ema, 0.5, 2.0)

Safety:  sources with fewer than `min_samples` recorded fetches are skipped
         (a single transient hiccup shouldn't nuke a source's weight).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from utils import DATA_DIR, PROJECT_ROOT, get_logger

SOURCES_PATH = os.path.join(PROJECT_ROOT, "sources.json")
METRICS_PATH = os.path.join(DATA_DIR, "source_metrics.json")
MIN_SAMPLES_DEFAULT = 5
WEIGHT_FLOOR = 0.5
WEIGHT_CEIL = 2.0

log = get_logger("auto_reweight")


def compute_weight(metrics_entry: dict, min_samples: int = MIN_SAMPLES_DEFAULT) -> float | None:
    """Return the proposed weight for a source, or None if not enough samples."""
    if metrics_entry.get("fetches_total", 0) < min_samples:
        return None
    sr = float(metrics_entry.get("success_rate_ema", 1.0))
    sr = max(0.0, min(1.0, sr))  # clamp input
    return round(max(WEIGHT_FLOOR, min(WEIGHT_CEIL, 0.5 + 1.5 * sr)), 2)


def _load_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error("failed to read %s: %s", path, e)
        return None


def apply_reweight(
    sources_path: str = SOURCES_PATH,
    metrics_path: str = METRICS_PATH,
    *,
    apply: bool = False,
    min_samples: int = MIN_SAMPLES_DEFAULT,
) -> list[tuple[str, float, float]]:
    """Compute changes; return (name, old_weight, new_weight) for sources that would change.

    Writes sources.json only when apply=True. Sources without enough samples are skipped.
    """
    sources_doc = _load_json(sources_path)
    metrics = _load_json(metrics_path) or {}

    if sources_doc is None:
        log.error("sources.json not found at %s", sources_path)
        return []

    changes: list[tuple[str, float, float]] = []
    for src in sources_doc.get("sources", []):
        name = src.get("name")
        if name is None:
            continue
        m = metrics.get(name)
        if not m:
            continue
        new_w = compute_weight(m, min_samples=min_samples)
        if new_w is None:
            continue
        old_w = float(src.get("weight", 1.0))
        if abs(new_w - old_w) < 0.005:
            continue
        changes.append((name, old_w, new_w))
        if apply:
            src["weight"] = new_w

    if apply and changes:
        with open(sources_path, "w", encoding="utf-8") as f:
            json.dump(sources_doc, f, ensure_ascii=False, indent=2)

    return changes


def _print_changes(changes: list[tuple[str, float, float]], *, applied: bool) -> None:
    header = ("source", "current", "proposed", "delta")
    if not changes:
        print("(no proposed weight changes — either no metrics yet or all sources are stable)")
        return
    rows = [(name, f"{old:.2f}", f"{new:.2f}", f"{new - old:+.2f}") for name, old, new in changes]
    width = [max(len(str(r[i])) for r in [header, *rows]) for i in range(4)]
    fmt = "  ".join(f"{{:<{w}}}" for w in width)
    print(fmt.format(*header))
    print(fmt.format(*["-" * w for w in width]))
    for r in rows:
        print(fmt.format(*r))
    print()
    print(f"{'Wrote' if applied else 'Would write'} {len(changes)} change(s) to sources.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-tune sources.json weights from rolling metrics")
    parser.add_argument("--apply", action="store_true",
                        help="Write changes to sources.json (default: dry-run)")
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES_DEFAULT,
                        help=f"Skip sources with fewer fetches (default: {MIN_SAMPLES_DEFAULT})")
    args = parser.parse_args()

    changes = apply_reweight(apply=args.apply, min_samples=args.min_samples)
    _print_changes(changes, applied=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
