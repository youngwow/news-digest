#!/usr/bin/env python3
"""
Pipeline health checker for the news digest project.
Validates all pipeline outputs and produces a health.json report.

Run: python3 health_check.py [--quiet] [--max-age-minutes N]

Exit codes:
  0 — all healthy
  1 — warnings (non-critical issues)
  2 — critical (pipeline likely broken, outputs unusable)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))

FILES_TO_CHECK = {
    "raw_news.json": {
        "required_keys": ["articles", "total", "collected_at"],
        "min_articles": 1,
        "desc": "RSS scraper output",
    },
    "analyzed_news.json": {
        "required_keys": ["articles", "input_count", "deduped_at"],
        "min_articles": 1,
        "desc": "Deduplicated & analyzed articles",
    },
    "digest.json": {
        "required_keys": ["digest", "total_unique"],
        "min_articles": None,  # checked via nested key
        "desc": "Structured digest",
    },
    "digest.md": {
        "required_keys": None,  # text file, not JSON
        "min_size_bytes": 200,
        "desc": "Markdown digest",
    },
}

# Source success threshold: at least this many must respond
MIN_SOURCES_OK = 3
# How many sources exist in sources.json (for ratio check)
EXPECTED_SOURCES = 10
# Max age before we consider output stale
DEFAULT_MAX_AGE_MINUTES = 360  # 6 hours


def load_json(path: str):
    """Load and parse a JSON file. Returns (data, error_string)."""
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


def get_timestamp_age(data: dict, keys: list[str]) -> float | None:
    """Extract timestamp from data using one of the given keys.
    Returns age in minutes, or None if no timestamp found."""
    for key in keys:
        ts = data.get(key)
        if not ts:
            continue
        try:
            # Handle ISO 8601 with timezone
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return (now - dt).total_seconds() / 60
        except (ValueError, TypeError):
            continue
    return None


def check_raw_news(path: str) -> dict:
    """Check scraper output."""
    result = {"file": path, "status": "unknown", "issues": [], "details": {}}

    data, err = load_json(path)
    if err:
        result["status"] = "critical"
        result["issues"].append(f"raw_news.json: {err}")
        return result

    art = data.get("articles", [])
    total = data.get("total", 0)
    sources_ok = data.get("sources_ok", 0)
    sources_fail = data.get("sources_fail", 0)

    result["details"] = {
        "articles": len(art),
        "total": total,
        "sources_ok": sources_ok,
        "sources_fail": sources_fail,
    }

    age = get_timestamp_age(data, ["collected_at"])
    if age is not None:
        result["details"]["age_minutes"] = round(age, 1)

    issues = []

    if len(art) == 0:
        issues.append(("critical", f"0 articles scraped (all {sources_fail}/{EXPECTED_SOURCES} sources failed)"))
    elif total == 0:
        issues.append(("critical", "total=0 — no articles collected"))

    if sources_ok < MIN_SOURCES_OK:
        issues.append(("warning", f"only {sources_ok}/{EXPECTED_SOURCES} sources responded (threshold: {MIN_SOURCES_OK})"))

    if sources_fail > EXPECTED_SOURCES - MIN_SOURCES_OK:
        issues.append(("warning", f"{sources_fail} sources failed — may indicate network/blocking issues"))

    result["issues"] = issues
    result["status"] = derive_status(issues)
    return result


def check_analyzed_news(path: str, max_age: int) -> dict:
    """Check analyzer output."""
    result = {"file": path, "status": "unknown", "issues": [], "details": {}}

    data, err = load_json(path)
    if err:
        result["status"] = "critical"
        result["issues"].append(f"analyzed_news.json: {err}")
        return result

    articles = data.get("articles", [])
    input_count = data.get("input_count", 0)
    result["details"] = {
        "groups": len(articles),
        "input_count": input_count,
    }

    age = get_timestamp_age(data, ["deduped_at", "analyzed_at"])
    if age is not None:
        result["details"]["age_minutes"] = round(age, 1)

    issues = []

    if len(articles) == 0:
        issues.append(("critical", "0 article groups after dedup"))
    else:
        # Check if analysis was completed (category/importance filled)
        unanalyzed = sum(1 for a in articles if a.get("category") is None)
        result["details"]["unanalyzed"] = unanalyzed
        if unanalyzed > 0:
            issues.append(("warning", f"{unanalyzed}/{len(articles)} groups have no category — analysis incomplete"))

        # Check importance distribution
        imps = [a.get("importance", 0) for a in articles]
        if imps:
            result["details"]["avg_importance"] = round(sum(imps) / len(imps), 1)
            result["details"]["max_importance"] = max(imps)

        # Category coverage
        cats = [a.get("category", "прочее") for a in articles]
        from collections import Counter
        result["details"]["category_dist"] = dict(Counter(cats))

    if age is not None and age > max_age:
        issues.append(("warning", f"output is {age:.0f} min old (threshold: {max_age} min)"))

    result["issues"] = issues
    result["status"] = derive_status(issues)
    return result


def check_digest_json(path: str) -> dict:
    """Check digest.json."""
    result = {"file": path, "status": "unknown", "issues": [], "details": {}}

    data, err = load_json(path)
    if err:
        result["status"] = "critical"
        result["issues"].append(f"digest.json: {err}")
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


def check_digest_md(path: str) -> dict:
    """Check digest.md."""
    result = {"file": path, "status": "unknown", "issues": [], "details": {}}

    if not os.path.exists(path):
        result["status"] = "critical"
        result["issues"].append("digest.md: file not found")
        return result

    try:
        size = os.path.getsize(path)
    except OSError as e:
        result["status"] = "critical"
        result["issues"].append(f"digest.md: {e}")
        return result

    result["details"]["size_bytes"] = size

    issues = []
    if size < 200:
        issues.append(("critical", f"digest.md is only {size} bytes — likely empty or broken"))

    result["issues"] = issues
    result["status"] = derive_status(issues)
    return result


def check_pipeline_status() -> dict:
    """Check pipeline_status.json (written by run.sh) for step-level failures."""
    status_path = os.path.join(HERE, "pipeline_status.json")
    result = {"file": status_path, "status": "unknown", "issues": [], "details": {}}

    data, err = load_json(status_path)
    if err:
        if "file not found" in err:
            # No status file — pipeline hasn't run yet
            result["status"] = "warning"
            result["issues"].append(("warning", "pipeline_status.json: not found — pipeline may not have run yet"))
            result["details"]["note"] = "status file doesn't exist yet"
            return result
        result["status"] = "critical"
        result["issues"].append(f"pipeline_status.json: {err}")
        return result

    if data is None:  # defensive — shouldn't happen if no err
        result["status"] = "critical"
        result["issues"].append("pipeline_status.json: loaded but data is null")
        return result

    overall = data.get("overall", "unknown")
    total = data.get("total_steps", 0)
    failed_count = data.get("failed_count", 0)
    failed_steps = data.get("failed_steps", [])
    steps = data.get("steps", [])

    result["details"] = {
        "overall": overall,
        "total_steps": total,
        "failed_count": failed_count,
        "pipeline_run_at": data.get("pipeline_run_at"),
    }

    issues = []

    if failed_count > 0:
        for step in steps:
            if step.get("status") == "failed" or step.get("exit_code", 0) != 0:
                step_name = step.get("step", "unknown")
                exit_code = step.get("exit_code", "?")
                desc = step.get("description", "")
                issues.append(("critical",
                    f"step '{step_name}' failed (exit {exit_code}): {desc}"))

        # Also include human-friendly failed steps list
        if failed_steps:
            result["details"]["failed_steps"] = failed_steps

    elif total > 0 and overall == "ok":
        # All steps passed
        result["details"]["all_steps_passed"] = True

    if not steps and not err:
        issues.append(("warning", "pipeline_status.json has no step records"))

    result["issues"] = issues
    result["status"] = derive_status(issues)
    return result


def check_pipeline_log() -> dict:
    """Check pipeline.log for recent errors."""
    log_path = os.path.join(HERE, "pipeline.log")
    result = {"file": log_path, "status": "healthy", "issues": [], "details": {}}

    if not os.path.exists(log_path):
        # No log yet — first run hasn't happened, not an error
        result["details"]["note"] = "log file doesn't exist yet"
        return result

    try:
        with open(log_path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return result

    result["details"]["total_lines"] = len(lines)

    # Check last 50 lines for FAILED markers
    recent = lines[-50:] if len(lines) > 50 else lines
    failed_lines = [l for l in recent if "FAILED" in l]
    if failed_lines:
        result["issues"].append(("warning", f"{len(failed_lines)} FAILED markers in last 50 log lines"))
        result["details"]["recent_failures"] = len(failed_lines)
        result["status"] = "warning"

    return result


def derive_status(issues: list[tuple[str, str]]) -> str:
    """Derive overall status from issues list."""
    if not issues:
        return "healthy"
    severities = {s for s, _ in issues}
    if "critical" in severities:
        return "critical"
    return "warning"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="News digest pipeline health check")
    parser.add_argument("--quiet", action="store_true", help="Suppress stdout, exit code only")
    parser.add_argument("--max-age-minutes", type=int, default=DEFAULT_MAX_AGE_MINUTES,
                        help=f"Max age of pipeline outputs in minutes (default: {DEFAULT_MAX_AGE_MINUTES})")
    parser.add_argument("--json", action="store_true", help="Output as JSON line instead of human-readable")
    args = parser.parse_args()

    checks = {}

    # Stage 1: raw_news.json
    checks["raw_news"] = check_raw_news(os.path.join(HERE, "raw_news.json"))

    # Stage 2: analyzed_news.json
    checks["analyzed_news"] = check_analyzed_news(
        os.path.join(HERE, "analyzed_news.json"), args.max_age_minutes
    )

    # Stage 3a: digest.json
    checks["digest_json"] = check_digest_json(os.path.join(HERE, "digest.json"))

    # Stage 3b: digest.md
    checks["digest_md"] = check_digest_md(os.path.join(HERE, "digest.md"))

    # Log file
    checks["pipeline_log"] = check_pipeline_log()

    # Pipeline step-level status (from run.sh's pipeline_status.json)
    checks["pipeline_status"] = check_pipeline_status()

    # Determine overall status
    statuses = [c["status"] for c in checks.values()]
    if "critical" in statuses:
        overall = "critical"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "healthy"

    # Build report
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "checks": checks,
    }

    # Save report
    report_path = os.path.join(HERE, "health.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if args.json:
        print(json.dumps({"overall": overall, "checks": {k: {"status": v["status"], "issues": v["issues"]}
                                                          for k, v in checks.items()}},
                         ensure_ascii=False))
        sys.exit({"healthy": 0, "warning": 1, "critical": 2}[overall])

    if not args.quiet:
        print(f"📊 Health check: {overall.upper()}")
        print()

        for name, check in checks.items():
            icon = {"healthy": "✅", "warning": "⚠️", "critical": "❌"}[check["status"]]
            desc = {
                "pipeline_status": "Pipeline steps",
                "raw_news": "RSS scraper",
                "analyzed_news": "Dedup & analysis",
                "digest_json": "Digest JSON",
                "digest_md": "Digest markdown",
                "pipeline_log": "Pipeline log",
            }[name]

            print(f"  {icon} {desc}: {check['status']}")
            for severity, msg in check.get("issues", []):
                print(f"     [{severity}] {msg}")

        print()
        print(f"Report saved: {report_path}")

    sys.exit({"healthy": 0, "warning": 1, "critical": 2}[overall])


if __name__ == "__main__":
    main()
