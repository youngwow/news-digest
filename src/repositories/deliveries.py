"""SQLite-репозиторий доставок дайджеста (v9): что ушло читателю и когда."""

from __future__ import annotations

import sqlite3
from typing import Iterable

from ..models import DigestDelivery


class DeliveryRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, delivery: DigestDelivery, items: Iterable[tuple[int, int | None]]) -> int:
        """Записать доставку и её карточки; `items` — пары (item_id, follow_up_of)."""
        cur = self.conn.execute(
            "INSERT INTO digest_deliveries (sent_at, chat_id, title, parts, items_count, body, "
            "trigger) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                delivery.sent_at,
                delivery.chat_id,
                delivery.title,
                delivery.parts,
                delivery.items_count,
                delivery.body,
                delivery.trigger,
            ),
        )
        delivery_id = int(cur.lastrowid)
        self.conn.executemany(
            "INSERT OR IGNORE INTO digest_delivery_items (delivery_id, item_id, follow_up_of) "
            "VALUES (?, ?, ?)",
            [(delivery_id, int(item_id), follow_up) for item_id, follow_up in items],
        )
        self.conn.commit()
        delivery.id = delivery_id
        return delivery_id

    def get(self, delivery_id: int) -> DigestDelivery | None:
        row = self.conn.execute(
            "SELECT * FROM digest_deliveries WHERE id = ?", (delivery_id,)
        ).fetchone()
        return DigestDelivery.from_row(row) if row else None

    def last(self) -> DigestDelivery | None:
        row = self.conn.execute(
            "SELECT * FROM digest_deliveries ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return DigestDelivery.from_row(row) if row else None

    def list(self, limit: int = 20) -> list[DigestDelivery]:
        rows = self.conn.execute(
            "SELECT * FROM digest_deliveries ORDER BY id DESC LIMIT ?", (int(limit),)
        )
        return [DigestDelivery.from_row(r) for r in rows]

    def items(self, delivery_id: int) -> list[tuple[int, int | None]]:
        rows = self.conn.execute(
            "SELECT item_id, follow_up_of FROM digest_delivery_items WHERE delivery_id = ? "
            "ORDER BY item_id",
            (delivery_id,),
        )
        return [(int(r["item_id"]), r["follow_up_of"]) for r in rows]

    def delivered_since(self, since: str) -> list[int]:
        """Карточки, попавшие в дайджесты не раньше `since` (ISO UTC) — кандидаты в 🔄."""
        rows = self.conn.execute(
            "SELECT DISTINCT di.item_id FROM digest_delivery_items di "
            "JOIN digest_deliveries d ON d.id = di.delivery_id "
            "WHERE d.sent_at >= ? ORDER BY di.item_id",
            (since,),
        )
        return [int(r["item_id"]) for r in rows]

    def is_delivered(self, item_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM digest_delivery_items WHERE item_id = ? LIMIT 1", (item_id,)
        ).fetchone()
        return row is not None
