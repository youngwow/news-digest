"""Миграция до `PRAGMA user_version = 9`: доставки дайджеста.

База v8 (данные хакатона: источники, документы, карточки) должна доехать до v9
без потерь, получить пустую историю доставок и начать её вести.
"""

from __future__ import annotations

import pytest
from support import frozen_database

from src.models import DigestDelivery
from src.repositories import Database
from src.repositories.database import _MIGRATIONS

NOW = "2026-09-02T12:00:00+00:00"


@pytest.fixture
def v8_database(tmp_path) -> tuple[str, int]:
    path = str(tmp_path / "hub.db")
    conn = frozen_database(path, 8)
    source = int(
        conn.execute(
            "INSERT INTO sources (name, url, kind, category, fetch_url, status, normalized_url, "
            "poll_interval, created_at, notes) VALUES ('Хабр', 'https://t.me/habr_com', "
            "'telegram', 'telegram', 'https://t.me/s/habr_com', 'active', 't.me/habr_com', '1h', ?, '')",
            (NOW,),
        ).lastrowid
    )
    document = int(
        conn.execute(
            "INSERT INTO documents (source_id, external_id, url, title, fetched_at, created_at) "
            "VALUES (?, 'p1', 'https://t.me/habr_com/1', 'Пост', ?, ?)",
            (source, NOW, NOW),
        ).lastrowid
    )
    cluster = int(
        conn.execute(
            "INSERT INTO clusters (canonical_document_id, size, created_at) VALUES (?, 1, ?)",
            (document, NOW),
        ).lastrowid
    )
    item = int(
        conn.execute(
            "INSERT INTO items (cluster_id, type, title, summary, priority, tags, analyst_note, "
            "visibility, manual_overrides, processed_at, published_at) "
            "VALUES (?, 'news', 'Пост', 'Саммари', 'medium', '[]', '', 'visible', '[]', ?, ?)",
            (cluster, NOW, NOW),
        ).lastrowid
    )
    conn.execute(
        "INSERT INTO item_sources (item_id, document_id, is_canonical) VALUES (?, ?, 1)",
        (item, document),
    )
    conn.commit()
    conn.close()
    return path, item


def test_current_version_is_nine():
    assert max(_MIGRATIONS) == 9


def test_v8_database_migrates_and_keeps_its_cards(v8_database):
    path, item_id = v8_database
    db = Database(path)
    try:
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 9
        assert db.items.get(item_id).title == "Пост"
        assert db.deliveries.last() is None
        tables = {
            r[0]
            for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"digest_deliveries", "digest_delivery_items"} <= tables
        db.deliveries.add(DigestDelivery(sent_at=NOW, chat_id="42"), [(item_id, None)])
        assert db.deliveries.is_delivered(item_id)
    finally:
        db.close()


def test_migration_is_idempotent_on_reopen(v8_database):
    path, _ = v8_database
    Database(path).close()
    db = Database(path)
    try:
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 9
    finally:
        db.close()
