"""Датасет для дистилляции: документ и то, что о нём решила модель (и человек).

Каждая карточка — размеченный пример: текст оригинала, тип, приоритет, рубрики,
уверенность, кто правил. Правки аналитика (`manual_overrides`) — самые ценные
строки: там метка не модели, а человека. Набор копится бесплатно в базе, отсюда
он выгружается в JSONL для обучения маленького локального классификатора
(`docs/ml.md`: критерий — 300–500 правок приоритета).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Iterable

TEXT_LIMIT = 4000  # символов оригинала на строку: хвост для классификатора не нужен


@dataclass
class Example:
    item_id: int
    url: str
    title: str
    source: str
    published_at: str | None
    text: str
    type: str
    priority: str
    tags: list[str]
    relevance_score: float
    confidence: float
    degraded: bool
    edited_fields: list[str] = field(default_factory=list)
    model: str = ""
    processed_at: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def _json_list(raw) -> list:
    try:
        value = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return value if isinstance(value, list) else []


def load_examples(
    conn, *, since: str | None = None, include_degraded: bool = False, text_limit: int = TEXT_LIMIT
) -> list[Example]:
    """Видимые карточки с каноническим документом; одна строка на URL."""
    clauses = ["i.visibility = 'visible'", "s.is_canonical = 1"]
    params: list = []
    if not include_degraded:
        clauses.append("i.degraded = 0")
    if since:
        clauses.append("i.processed_at >= ?")
        params.append(since)
    rows = conn.execute(
        "SELECT i.id, i.type, i.priority, i.tags, i.relevance_score, i.confidence, i.degraded, "
        "i.manual_overrides, i.model_name, i.processed_at, i.published_at, i.title, "
        "d.url, d.title AS doc_title, d.norm_text, d.text, src.name AS source_name "
        "FROM items i JOIN item_sources s ON s.item_id = i.id "
        "JOIN documents d ON d.id = s.document_id "
        "LEFT JOIN sources src ON src.id = d.source_id "
        "WHERE " + " AND ".join(clauses) + " ORDER BY i.id",
        params,
    )
    seen: set[str] = set()
    examples: list[Example] = []
    for row in rows:
        url = row["url"] or ""
        if url in seen:
            continue
        seen.add(url)
        body = row["norm_text"] or row["text"] or ""
        examples.append(
            Example(
                item_id=int(row["id"]),
                url=url,
                title=row["doc_title"] or row["title"] or "",
                source=row["source_name"] or "",
                published_at=row["published_at"],
                text=body[:text_limit],
                type=row["type"],
                priority=row["priority"],
                tags=_json_list(row["tags"]),
                relevance_score=float(row["relevance_score"] or 0),
                confidence=float(row["confidence"] or 0),
                degraded=bool(row["degraded"]),
                edited_fields=[str(f) for f in _json_list(row["manual_overrides"])],
                model=row["model_name"] or "",
                processed_at=row["processed_at"] or "",
            )
        )
    return examples


def write_jsonl(examples: Iterable[Example], path: str) -> int:
    count = 0
    with open(path, "w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(example.to_json() + "\n")
            count += 1
    return count


def stats(examples: list[Example]) -> dict:
    """Сколько накопилось и как распределено — чтобы понимать, пора ли учить."""
    priorities = Counter(e.priority for e in examples)
    types = Counter(e.type for e in examples)
    tags = Counter(tag for e in examples for tag in e.tags)
    edited = [e for e in examples if e.edited_fields]
    edited_priority = sum(1 for e in edited if "priority" in e.edited_fields)
    return {
        "examples": len(examples),
        "by_priority": dict(sorted(priorities.items())),
        "by_type": dict(sorted(types.items())),
        "by_tag": dict(tags.most_common()),
        "edited": len(edited),
        "edited_priority": edited_priority,
        "sources": len({e.source for e in examples if e.source}),
        "with_text": sum(1 for e in examples if e.text),
    }


def format_stats(data: dict) -> str:
    lines = [
        f"примеров: {data['examples']} (с текстом {data['with_text']}, источников {data['sources']})",
        "приоритет: " + ", ".join(f"{k} {v}" for k, v in data["by_priority"].items()),
        "тип: " + ", ".join(f"{k} {v}" for k, v in data["by_type"].items()),
        "рубрики: " + (", ".join(f"{k} {v}" for k, v in data["by_tag"].items()) or "—"),
        f"правлено человеком: {data['edited']} (приоритет — {data['edited_priority']})",
    ]
    return "\n".join(lines)
