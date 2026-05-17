#!/usr/bin/env python3
"""Shared utilities for the news-digest pipeline."""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))          # src/
PROJECT_ROOT = os.path.dirname(HERE)                        # project root
DATA_DIR = os.path.join(PROJECT_ROOT, "data")


# Required keys: section -> {key: expected_type}. Used by _validate_config.
_REQUIRED_SCHEMA: dict[str, dict[str, type | tuple[type, ...]]] = {
    "llm": {
        "active": str, "providers": list,
        "classify_concurrency": int, "cache_retention_days": int,
    },
    "scraper": {
        "date_window_hours": (int, float), "request_timeout": (int, float),
        "max_redirects": int, "user_agent": str,
        "fetch_bodies": bool, "body_max_chars": int,
        "body_timeout": (int, float), "body_concurrency": int,
        "body_cache_retention_days": int,
    },
    "pipeline": {"chunk_size": int},
    "telegram": {"message_limit": int},
    "health": {
        "min_sources_ok": int, "expected_sources": int,
        "default_max_age_minutes": int,
    },
    "dedup": {
        "overlap_threshold": (int, float), "shared_words_min": int,
        "containment_min_len": int,
        "cross_run_enabled": bool, "cross_run_retention_days": int,
    },
    "archive": {"retention_days": int},
    "heuristics": {
        "multi_source_max_boost": int, "recency_window_hours": (int, float),
        "recency_boost": int, "source_weight_max_boost": int,
    },
    "categories": {"order": list, "emoji": dict, "labels": dict},
}


def _validate_config(cfg: dict) -> None:
    """Fail fast with a clear message if config.yaml is missing or malformed."""
    if not isinstance(cfg, dict):
        raise SystemExit("config.yaml: top level must be a mapping")

    for section, keys in _REQUIRED_SCHEMA.items():
        if section not in cfg:
            raise SystemExit(f"config.yaml: missing section '{section}'")
        if not isinstance(cfg[section], dict):
            raise SystemExit(f"config.yaml: section '{section}' must be a mapping")
        for key, expected_type in keys.items():
            if key not in cfg[section]:
                raise SystemExit(f"config.yaml: missing key '{section}.{key}'")
            if not isinstance(cfg[section][key], expected_type):
                raise SystemExit(
                    f"config.yaml: '{section}.{key}' must be {expected_type}, "
                    f"got {type(cfg[section][key]).__name__}"
                )

    order = cfg["categories"]["order"]
    for cat in order:
        if cat not in cfg["categories"]["emoji"]:
            raise SystemExit(f"config.yaml: categories.emoji missing entry for '{cat}'")
        if cat not in cfg["categories"]["labels"]:
            raise SystemExit(f"config.yaml: categories.labels missing entry for '{cat}'")

    _validate_llm_providers(cfg["llm"])


_PROVIDER_REQUIRED: dict[str, type | tuple[type, ...]] = {
    "name": str, "base_url": str, "model": str,
    "temperature": (int, float), "max_tokens": int,
    "timeout": (int, float), "max_retries": int,
}


def _validate_llm_providers(llm: dict) -> None:
    providers = llm["providers"]
    if not providers:
        raise SystemExit("config.yaml: llm.providers must contain at least one entry")
    names: list[str] = []
    for i, p in enumerate(providers):
        if not isinstance(p, dict):
            raise SystemExit(f"config.yaml: llm.providers[{i}] must be a mapping")
        for key, expected in _PROVIDER_REQUIRED.items():
            if key not in p:
                raise SystemExit(f"config.yaml: llm.providers[{i}] missing key '{key}'")
            if not isinstance(p[key], expected):
                raise SystemExit(
                    f"config.yaml: llm.providers[{i}].{key} must be {expected}, "
                    f"got {type(p[key]).__name__}"
                )
        if "api_key_env" in p and p["api_key_env"] is not None and not isinstance(p["api_key_env"], str):
            raise SystemExit(
                f"config.yaml: llm.providers[{i}].api_key_env must be string or null"
            )
        names.append(p["name"])

    if llm["active"] not in names:
        raise SystemExit(
            f"config.yaml: llm.active '{llm['active']}' does not match any provider name "
            f"(available: {names})"
        )


def _load_config() -> dict:
    path = os.path.join(PROJECT_ROOT, "config.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        raise SystemExit(f"config.yaml not found at {path}")
    _validate_config(cfg)
    return cfg


CONFIG: dict = _load_config()


_LOGGING_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    """Return a logger with the project's standard format (stderr)."""
    global _LOGGING_CONFIGURED
    if not _LOGGING_CONFIGURED:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        _LOGGING_CONFIGURED = True
    return logging.getLogger(name)


def load_env_secret(var_name: str) -> str:
    """Return `var_name` from process environment or PROJECT_ROOT/.env (first match)."""
    val = os.environ.get(var_name, "")
    if val:
        return val
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        prefix = f"{var_name}="
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(prefix):
                    raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if raw:
                        return raw
    return ""


def load_api_key() -> str:
    """Backwards-compat wrapper: return OLLAMA_API_KEY from env or .env."""
    return load_env_secret("OLLAMA_API_KEY")


def extract_json(text: str) -> str:
    """Strip markdown code fences and return the outermost JSON value ({} or []).

    Returns "" if no JSON-looking content can be located, so callers get a clean
    JSONDecodeError rather than receiving partial garbage.
    """
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    brace = text.find("{")
    bracket = text.find("[")
    if brace == -1 and bracket == -1:
        return ""
    if brace == -1:
        start, close = bracket, "]"
    elif bracket == -1:
        start, close = brace, "}"
    else:
        start, close = (brace, "}") if brace < bracket else (bracket, "]")
    end = text.rfind(close)
    if end <= start:
        return ""
    return text[start:end + 1]


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: dict | list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── cross-run URL dedup ─────────────────────────────────────────

SEEN_URLS_PATH = os.path.join(DATA_DIR, "seen_urls.json")


def load_seen_urls() -> dict[str, str]:
    """Return {url: ISO timestamp} for previously-delivered URLs, or {} if absent."""
    if not os.path.exists(SEEN_URLS_PATH):
        return {}
    try:
        with open(SEEN_URLS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _prune_seen_urls(urls: dict[str, str], retention_days: int) -> dict[str, str]:
    """Drop entries older than retention_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept: dict[str, str] = {}
    for url, ts in urls.items():
        try:
            seen_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            continue
        if seen_at >= cutoff:
            kept[url] = ts
    return kept


def mark_urls_seen(new_urls: Iterable[str], retention_days: int) -> None:
    """Record `new_urls` as delivered now; prune entries older than retention_days."""
    existing = load_seen_urls()
    now = datetime.now(timezone.utc).isoformat()
    for url in new_urls:
        if url and url not in existing:
            existing[url] = now
    pruned = _prune_seen_urls(existing, retention_days)
    os.makedirs(DATA_DIR, exist_ok=True)
    save_json(SEEN_URLS_PATH, pruned)
