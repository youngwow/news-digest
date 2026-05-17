#!/usr/bin/env python3
"""Shared utilities for the news-digest pipeline."""

import json
import logging
import os
import re

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))          # src/
PROJECT_ROOT = os.path.dirname(HERE)                        # project root
DATA_DIR = os.path.join(PROJECT_ROOT, "data")


# Required keys: section -> {key: expected_type}. Used by _validate_config.
_REQUIRED_SCHEMA: dict[str, dict[str, type | tuple[type, ...]]] = {
    "llm": {
        "base_url": str, "model": str, "temperature": (int, float),
        "max_tokens": int, "timeout": (int, float),
        "max_retries": int, "classify_concurrency": int,
    },
    "scraper": {
        "date_window_hours": (int, float), "request_timeout": (int, float),
        "max_redirects": int, "user_agent": str,
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


def load_api_key() -> str:
    """Return OLLAMA_API_KEY from environment or local .env file."""
    key = os.environ.get("OLLAMA_API_KEY", "")
    if key:
        return key
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("OLLAMA_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    return ""


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
