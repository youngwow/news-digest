"""`dataset` — размеченные карточки как JSONL и статистика накопления."""

from __future__ import annotations

import json

from src import cli
from src.models import RawDocument, Source
from src.processing import dataset

NOW = "2026-09-02T12:00:00+00:00"


def _cards(db, item_factory) -> list[int]:
    source = db.sources.add(
        Source(name="Хабр", url="https://t.me/habr_com", kind="telegram", fetch_url="https://t.me/s/habr_com")
    )
    ids = []
    with db.transaction():
        for index, url in enumerate(["https://t.me/habr_com/1", "https://t.me/habr_com/2", "https://t.me/habr_com/2"]):
            doc = db.documents.insert(
                RawDocument(
                    source_id=source.id, external_id=f"p{index}", url=url, title=f"Пост {index}",
                    text="Текст поста " * 3, published_at=NOW, fetched_at=NOW,
                )
            )
            db.documents.set_derived(doc, norm_text=f"Нормализованный текст {index}")
            ids.append(doc)
    return [
        item_factory(db, ids[0], priority="high", tags=["ии"], processed_at="2026-09-01T00:00:00+00:00"),
        item_factory(db, ids[1], priority="low", tags=["ии", "финансы"], manual_overrides=["priority"]),
        item_factory(db, ids[2], priority="medium", degraded=1),  # тот же URL, деградировавшая
    ]


def test_examples_join_document_text_with_labels_and_dedupe_by_url(db, item_factory):
    _cards(db, item_factory)
    examples = dataset.load_examples(db.conn)
    assert [e.url for e in examples] == ["https://t.me/habr_com/1", "https://t.me/habr_com/2"]
    first = examples[0]
    assert (first.priority, first.tags, first.source, first.text) == ("high", ["ии"], "Хабр", "Нормализованный текст 0")
    assert examples[1].edited_fields == ["priority"]
    assert dataset.load_examples(db.conn, include_degraded=True)[1].degraded is False  # дубль URL отброшен
    assert [e.item_id for e in dataset.load_examples(db.conn, since="2026-09-02T00:00:00+00:00")] == [2]


def test_stats_count_labels_and_human_edits(db, item_factory):
    _cards(db, item_factory)
    data = dataset.stats(dataset.load_examples(db.conn))
    assert data["examples"] == 2 and data["by_priority"] == {"high": 1, "low": 1}
    assert data["by_tag"] == {"ии": 2, "финансы": 1}
    assert (data["edited"], data["edited_priority"], data["sources"]) == (1, 1, 1)
    text = dataset.format_stats(data)
    assert "примеров: 2" in text and "правлено человеком: 1 (приоритет — 1)" in text


def test_cli_writes_jsonl_and_prints_stats(config, hub_paths, file_db, item_factory, tmp_path, capsys):
    _cards(file_db, item_factory)
    out = tmp_path / "dataset.jsonl"
    args = cli.build_parser().parse_args(["dataset", "--out", str(out)])
    assert cli._cmd_dataset(args, config, hub_paths) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["priority"] == "high"
    printed = capsys.readouterr().out
    assert printed.startswith(f"2 строк → {out}") and "приоритет: high 1, low 1" in printed
