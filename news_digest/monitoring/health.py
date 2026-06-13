"""HealthChecker: validate pipeline outputs and produce a health report.

Each check returns {file, status, issues, details}; `issues` are always
(severity, message) tuples so the renderers/watchdog can unpack them safely.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone

from ..config import Config
from ..jsonio import get_logger, save_json
from ..paths import ProjectPaths

log = get_logger("health")


def derive_status(issues: list[tuple[str, str]]) -> str:
    if not issues:
        return "healthy"
    return "critical" if any(sev == "critical" for sev, _ in issues) else "warning"


def _read_json(path: str):
    """Return (data, error_string)."""
    if not os.path.exists(path):
        return None, "file not found"
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        return None, f"read error: {e}"
    if not raw.strip():
        return None, "file is empty"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e}"


def _timestamp_age(data: dict, keys: list[str]) -> float | None:
    """Age in minutes from the first present timestamp key, or None."""
    for key in keys:
        ts = data.get(key)
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - dt).total_seconds() / 60
        except (ValueError, TypeError):
            continue
    return None


# Human-readable labels for each check (English / Russian) used by renderers.
CHECK_LABELS = {
    "raw_news": ("RSS scraper", "Скрапинг RSS"),
    "classified": ("LLM classification", "Классификация LLM"),
    "digest_json": ("Digest JSON", "Digest JSON"),
    "digest_md": ("Digest markdown", "Digest Markdown"),
    "pipeline_status": ("Pipeline steps", "Шаги пайплайна"),
    "source_metrics": ("Source health", "Здоровье источников"),
}


class HealthChecker:
    def __init__(self, config: Config, paths: ProjectPaths):
        self.config = config
        self.health = config.health
        self.paths = paths

    def check_source_metrics(self, path: str) -> dict:
        result = {"file": path, "status": "healthy", "issues": [], "details": {}}
        if not os.path.exists(path):
            result["details"]["note"] = "no source_metrics.json yet"
            return result
        try:
            with open(path, encoding="utf-8") as f:
                metrics = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            result["status"] = "warning"
            result["issues"].append(("warning", f"source_metrics.json: {e}"))
            return result
        result["details"]["total_sources"] = len(metrics)
        for name, m in metrics.items():
            rate = m.get("success_rate_ema")
            if isinstance(rate, (int, float)) and rate < 0.5:
                result["issues"].append(
                    ("warning", f"source '{name}' rolling success rate is {round(rate, 2)} (< 0.5)"))
        result["status"] = derive_status(result["issues"])
        return result

    def check_raw_news(self, path: str) -> dict:
        result = {"file": path, "status": "unknown", "issues": [], "details": {}}
        data, err = _read_json(path)
        if err:
            result["status"] = "critical"
            result["issues"].append(("critical", f"raw_news.json: {err}"))
            return result
        art = data.get("articles", [])
        total = data.get("total", 0)
        sources_ok = data.get("sources_ok", 0)
        sources_fail = data.get("sources_fail", 0)
        result["details"] = {"articles": len(art), "total": total,
                             "sources_ok": sources_ok, "sources_fail": sources_fail}
        age = _timestamp_age(data, ["collected_at"])
        if age is not None:
            result["details"]["age_minutes"] = round(age, 1)
        issues = []
        if len(art) == 0:
            issues.append(("critical",
                           f"0 articles scraped (all {sources_fail}/{self.health.expected_sources} failed)"))
        elif total == 0:
            issues.append(("critical", "total=0 — no articles collected"))
        if sources_ok < self.health.min_sources_ok:
            issues.append(("warning", f"only {sources_ok}/{self.health.expected_sources} sources "
                                      f"responded (threshold: {self.health.min_sources_ok})"))
        if sources_fail > self.health.expected_sources - self.health.min_sources_ok:
            issues.append(("warning", f"{sources_fail} sources failed — possible network/blocking"))
        result["issues"] = issues
        result["status"] = derive_status(issues)
        return result

    def check_classified(self, path: str, max_age: int) -> dict:
        result = {"file": path, "status": "unknown", "issues": [], "details": {}}
        data, err = _read_json(path)
        if err:
            result["status"] = "critical"
            result["issues"].append(("critical", f"classified.json: {err}"))
            return result
        stories = data.get("stories", [])
        result["details"] = {"stories": len(stories), "input_count": data.get("input_count", 0)}
        age = _timestamp_age(data, ["classified_at"])
        if age is not None:
            result["details"]["age_minutes"] = round(age, 1)
        issues = []
        if len(stories) == 0:
            issues.append(("critical", "0 stories after classification — LLM step likely failed"))
        else:
            uncategorized = sum(1 for s in stories if not s.get("category"))
            result["details"]["uncategorized"] = uncategorized
            if uncategorized > 0:
                issues.append(("warning", f"{uncategorized}/{len(stories)} stories have no category"))
            imps = [s.get("importance", 0) for s in stories]
            if imps:
                result["details"]["avg_importance"] = round(sum(imps) / len(imps), 1)
                result["details"]["max_importance"] = max(imps)
            result["details"]["category_dist"] = dict(
                Counter(s.get("category", "прочее") for s in stories))
        if age is not None and age > max_age:
            issues.append(("warning", f"output is {age:.0f} min old (threshold: {max_age} min)"))
        result["issues"] = issues
        result["status"] = derive_status(issues)
        return result

    def check_digest_json(self, path: str) -> dict:
        result = {"file": path, "status": "unknown", "issues": [], "details": {}}
        data, err = _read_json(path)
        if err:
            result["status"] = "critical"
            result["issues"].append(("critical", f"digest.json: {err}"))
            return result
        digest = data.get("digest", {})
        result["details"] = {
            "total_unique": data.get("total_unique", 0),
            "has_headline": bool(digest.get("headline")),
            "top5_count": len(digest.get("top5", [])),
            "rubric_count": len(digest.get("rubrics", {})),
            "rest_count": len(digest.get("rest", [])),
        }
        issues = []
        if not digest.get("headline"):
            issues.append(("warning", "no headline in digest"))
        if len(digest.get("top5", [])) == 0:
            issues.append(("warning", "top5 is empty"))
        if data.get("total_unique", 0) == 0:
            issues.append(("critical", "total_unique=0 — digest has no stories"))
        result["issues"] = issues
        result["status"] = derive_status(issues)
        return result

    def check_digest_md(self, path: str) -> dict:
        result = {"file": path, "status": "unknown", "issues": [], "details": {}}
        if not os.path.exists(path):
            result["status"] = "critical"
            result["issues"].append(("critical", "digest.md: file not found"))
            return result
        try:
            size = os.path.getsize(path)
        except OSError as e:
            result["status"] = "critical"
            result["issues"].append(("critical", f"digest.md: {e}"))
            return result
        result["details"]["size_bytes"] = size
        issues = []
        if size < 200:
            issues.append(("critical", f"digest.md is only {size} bytes — likely empty or broken"))
        result["issues"] = issues
        result["status"] = derive_status(issues)
        return result

    def check_pipeline_status(self) -> dict:
        path = self.paths.data("pipeline_status.json")
        result = {"file": path, "status": "unknown", "issues": [], "details": {}}
        data, err = _read_json(path)
        if err:
            if "file not found" in err:
                result["status"] = "warning"
                result["issues"].append(("warning", "pipeline_status.json: not found — pipeline "
                                                    "may not have run yet"))
                result["details"]["note"] = "status file doesn't exist yet"
                return result
            result["status"] = "critical"
            result["issues"].append(("critical", f"pipeline_status.json: {err}"))
            return result

        steps = data.get("steps", [])
        result["details"] = {
            "overall": data.get("overall", "unknown"),
            "total_steps": data.get("total_steps", 0),
            "failed_count": data.get("failed_count", 0),
            "pipeline_run_at": data.get("pipeline_run_at"),
        }
        issues = []
        if data.get("failed_count", 0) > 0:
            # Only "failed" steps flagged; "skipped" downstream steps are covered
            # by the original failure.
            for step in steps:
                if step.get("status") == "failed":
                    issues.append(("critical",
                                   f"step '{step.get('step', '?')}' failed "
                                   f"(exit {step.get('exit_code', '?')}): {step.get('description', '')}"))
            if data.get("failed_steps"):
                result["details"]["failed_steps"] = data["failed_steps"]
        elif data.get("total_steps", 0) > 0 and data.get("overall") == "ok":
            result["details"]["all_steps_passed"] = True
        if not steps:
            issues.append(("warning", "pipeline_status.json has no step records"))
        result["issues"] = issues
        result["status"] = derive_status(issues)
        return result

    def run(self, max_age: int | None = None) -> dict:
        max_age = max_age if max_age is not None else self.health.default_max_age_minutes
        checks = {
            "raw_news": self.check_raw_news(self.paths.data("raw_news.json")),
            "classified": self.check_classified(self.paths.data("classified.json"), max_age),
            "digest_json": self.check_digest_json(self.paths.data("digest.json")),
            "digest_md": self.check_digest_md(self.paths.data("digest.md")),
            "pipeline_status": self.check_pipeline_status(),
            "source_metrics": self.check_source_metrics(self.paths.data("source_metrics.json")),
        }
        statuses = [c["status"] for c in checks.values()]
        overall = ("critical" if "critical" in statuses
                   else "warning" if "warning" in statuses else "healthy")
        report = {"checked_at": datetime.now(timezone.utc).isoformat(),
                  "overall": overall, "checks": checks}
        os.makedirs(self.paths.data_dir, exist_ok=True)
        save_json(self.paths.data("health.json"), report)
        return report
