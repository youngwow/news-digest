"""Stateless text helpers (no config, no I/O)."""

from __future__ import annotations

import re

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def extract_json(text: str) -> str:
    """Strip markdown code fences and return the outermost JSON value ({} or []).

    Returns "" when no JSON-looking content can be located, so callers get a
    clean JSONDecodeError rather than partial garbage.
    """
    text = text.strip()
    m = _FENCE_RE.search(text)
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


def pluralize_ru(n: int, one: str, few: str, many: str) -> str:
    """Russian plural form for n: 1 сюжет / 2 сюжета / 5 сюжетов."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many
