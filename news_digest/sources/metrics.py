"""Per-source rolling metrics (EMA) and the auto-reweighter built on them."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from ..jsonio import get_logger, load_json, save_json

log = get_logger("source_metrics")

EMA_ALPHA = 0.3            # weight of a new observation in the rolling averages
WEIGHT_FLOOR = 0.5
WEIGHT_CEIL = 2.0
MIN_SAMPLES_DEFAULT = 5    # don't reweight a source on too few fetches


def ema(prev: float | None, current: float, alpha: float = EMA_ALPHA) -> float:
    """Single-step exponential moving average; seeds from `current` on first sample."""
    if prev is None:
        return float(current)
    return alpha * float(current) + (1 - alpha) * float(prev)


class SourceMetrics:
    """Rolling success-rate / latency / article-count stats per source."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict[str, dict]:
        if not os.path.exists(self.path):
            return {}
        try:
            data = load_json(self.path)
        except (ValueError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def update(self, samples: list[dict]) -> None:
        """Merge this run's per-source observations.

        Each sample: {name, success: bool, latency_ms: float, article_count: int}.
        """
        metrics = self.load()
        now = datetime.now(timezone.utc).isoformat()
        for s in samples:
            name = s["name"]
            entry = metrics.get(name, {})
            entry["fetches_total"] = entry.get("fetches_total", 0) + 1
            entry["successes_total"] = entry.get("successes_total", 0) + (1 if s["success"] else 0)
            entry["success_rate_ema"] = ema(entry.get("success_rate_ema"),
                                            1.0 if s["success"] else 0.0)
            entry["avg_latency_ms_ema"] = ema(entry.get("avg_latency_ms_ema"), s["latency_ms"])
            if s["success"]:
                entry["last_success"] = now
                entry["avg_articles_ema"] = ema(entry.get("avg_articles_ema"), s["article_count"])
            else:
                entry["last_failure"] = now
            metrics[name] = entry
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        save_json(self.path, metrics)


class Reweighter:
    """Recompute sources.json weights from rolling success rate.

    weight = clamp(0.5 + 1.5 * success_rate_ema, 0.5, 2.0), applied only to
    sources with >= min_samples recorded fetches.
    """

    def __init__(self, sources_path: str, metrics: SourceMetrics,
                 min_samples: int = MIN_SAMPLES_DEFAULT):
        self.sources_path = sources_path
        self.metrics = metrics
        self.min_samples = min_samples

    @staticmethod
    def compute_weight(entry: dict, min_samples: int) -> float | None:
        if entry.get("fetches_total", 0) < min_samples:
            return None
        sr = max(0.0, min(1.0, float(entry.get("success_rate_ema", 1.0))))
        return round(max(WEIGHT_FLOOR, min(WEIGHT_CEIL, 0.5 + 1.5 * sr)), 2)

    def propose(self, apply: bool = False) -> list[tuple[str, float, float]]:
        """Return (name, old_weight, new_weight) for sources that would change.

        Writes sources.json only when apply=True.
        """
        if not os.path.exists(self.sources_path):
            log.error("sources.json not found at %s", self.sources_path)
            return []
        try:
            doc = load_json(self.sources_path)
        except (ValueError, OSError) as e:
            log.error("failed to read %s: %s", self.sources_path, e)
            return []
        metrics = self.metrics.load()

        changes: list[tuple[str, float, float]] = []
        for src in doc.get("sources", []):
            name = src.get("name")
            m = metrics.get(name) if name else None
            if not m:
                continue
            new_w = self.compute_weight(m, self.min_samples)
            if new_w is None:
                continue
            old_w = float(src.get("weight", 1.0))
            if abs(new_w - old_w) < 0.005:
                continue
            changes.append((name, old_w, new_w))
            if apply:
                src["weight"] = new_w

        if apply and changes:
            save_json(self.sources_path, doc)
        return changes
