"""JSON load/save and .env secret reading (stateless I/O helpers)."""

from __future__ import annotations

import json
import logging
import os

_LOGGING_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    """Return a logger with the project's standard stderr format."""
    global _LOGGING_CONFIGURED
    if not _LOGGING_CONFIGURED:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        _LOGGING_CONFIGURED = True
    return logging.getLogger(name)


def load_json(path: str) -> dict | list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: dict | list) -> None:
    """Write JSON atomically (temp file + rename) so a crash mid-write can't
    leave a corrupt file behind — several callers persist cross-run state here."""
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def load_env_secret(var_name: str, env_path: str | None = None) -> str:
    """Return `var_name` from the process environment or an .env file."""
    val = os.environ.get(var_name, "")
    if val:
        return val
    if env_path and os.path.exists(env_path):
        prefix = f"{var_name}="
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(prefix):
                    raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if raw:
                        return raw
    return ""
