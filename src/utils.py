#!/usr/bin/env python3
"""Shared utilities for the news-digest pipeline."""

import json
import os
import re

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))          # src/
PROJECT_ROOT = os.path.dirname(HERE)                        # project root
DATA_DIR = os.path.join(PROJECT_ROOT, "data")


def _load_config() -> dict:
    path = os.path.join(PROJECT_ROOT, "config.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        raise SystemExit(f"config.yaml not found at {path}")


CONFIG: dict = _load_config()


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
    """Strip markdown code fences and return the outermost JSON value ({} or [])."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    brace = text.find("{")
    bracket = text.find("[")
    if brace == -1 and bracket == -1:
        return text
    if brace == -1:
        start, close = bracket, "]"
    elif bracket == -1:
        start, close = brace, "}"
    else:
        start, close = (brace, "}") if brace < bracket else (bracket, "]")
    end = text.rfind(close)
    if end > start:
        text = text[start:end + 1]
    return text


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: dict | list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
