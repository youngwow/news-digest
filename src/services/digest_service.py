"""DigestService — Telegram-дайджест поверх ленты: собрать, пометить продолжения,
отправить и запомнить, что читатель уже видел.

Срез берётся тем же `FeedService.visible_slice`, что и markdown-выгрузка, поэтому
скрытое и архив в чат не уходят. Продолжения (🔄) ищутся среди карточек,
доставленных за `digest.threads_lookback_days`, по косинусу эмбеддингов их
канонических документов — тех самых, что S1 посчитал при обработке; модель
для этого не грузится. Каждая доставка (и напечатанный `--record`) ложится в
`digest_deliveries`, и следующий `--undelivered` её карточки не повторяет.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import numpy as np

from ..config import Config
from ..delivery.telegram import DeliveryError, TelegramBot
from ..models import DigestDelivery
from ..models.queries import FeedQuery
from ..processing.dedup import decode_vector
from ..repositories import Database
from ..utils import get_logger, to_utc_iso, utc_now
from . import digest as render
from .feed_service import FeedService

log = get_logger("digest")


class DigestService:
    def __init__(
        self,
        config: Config,
        db: Database,
        feed: FeedService | None = None,
        bot: TelegramBot | None = None,
        env_path: str | None = None,
    ):
        self.config = config
        self.db = db
        self.feed = feed or FeedService(config, db)
        self.bot = bot or TelegramBot(config.telegram, env_path)

    # ── сборка ─────────────────────────────────────────────────────────────

    def compose(
        self,
        query: FeedQuery | None = None,
        *,
        title: str = "",
        undelivered: bool = False,
        limit: int = 200,
    ) -> render.DigestDocument:
        query = query or FeedQuery.build(order="priority", timezone_name=self.config.api.timezone)
        if undelivered:
            query = replace(query, undelivered=True)
        rows = self.feed.visible_slice(query, limit=limit)
        generated_at = to_utc_iso(utc_now()) or ""
        heading = title or f"Дайджест {render_date(generated_at)}"
        threads = self.threads(rows) if self.config.digest.threads_enabled else {}
        return render.compose(
            rows,
            categories=self.config.categories,
            settings=self.config.digest,
            title=heading,
            generated_at=generated_at,
            threads=threads,
        )

    def render(self, doc: render.DigestDocument) -> str:
        return render.to_telegram(doc, self.config.categories)

    # ── нити: продолжение уже доставленной истории ─────────────────────────

    def threads(self, rows: list[dict]) -> dict[int, int]:
        """item_id → id ранее доставленной карточки той же истории.

        Кандидаты — карточки из дайджестов за окно `threads_lookback_days`, векторы —
        `documents.embedding` канонических документов. Пара считается одной
        историей от `threads_similarity` и ниже порога S1 (выше S1 склеил бы сам).
        """
        settings = self.config.digest
        current = [int(r["id"]) for r in rows]
        if not current:
            return {}
        since = to_utc_iso(utc_now() - timedelta(days=settings.threads_lookback_days)) or ""
        previous_ids = [i for i in self.db.deliveries.delivered_since(since) if i not in current]
        if not previous_ids:
            return {}
        previous = self._embeddings(previous_ids)
        if not previous:
            return {}
        now_vectors = self._embeddings(current)
        if not now_vectors:
            return {}
        prev_ids = list(previous)
        matrix = np.asarray([previous[i] for i in prev_ids], dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0] = 1.0
        upper = self.config.processing.cosine_threshold
        result: dict[int, int] = {}
        for item_id, vector in now_vectors.items():
            v = np.asarray(vector, dtype=np.float32)
            if v.shape[0] != matrix.shape[1]:
                continue  # вектор прежней модели — не сравнивается
            norm = float(np.linalg.norm(v)) or 1.0
            scores = matrix @ v / (norms * norm)
            best = int(np.argmax(scores))
            score = float(scores[best])
            if settings.threads_similarity <= score < max(upper, settings.threads_similarity):
                result[item_id] = prev_ids[best]
            elif score >= upper:
                # выше порога S1 — это не продолжение, а тот же материал, доехавший
                # отдельной карточкой; помечаем тоже: читатель его уже видел
                result[item_id] = prev_ids[best]
        if result:
            log.info("продолжений историй: %d из %d карточек", len(result), len(now_vectors))
        return result

    def _embeddings(self, item_ids: list[int]) -> dict[int, list[float]]:
        if not item_ids:
            return {}
        marks = ",".join("?" * len(item_ids))
        rows = self.db.conn.execute(
            "SELECT s.item_id, d.embedding FROM item_sources s "
            "JOIN documents d ON d.id = s.document_id "
            f"WHERE s.is_canonical = 1 AND s.item_id IN ({marks})",
            item_ids,
        )
        vectors: dict[int, list[float]] = {}
        for row in rows:
            vector = decode_vector(row["embedding"])
            if vector:
                vectors[int(row["item_id"])] = vector
        return vectors

    # ── доставка и запись ──────────────────────────────────────────────────

    def record(
        self,
        doc: render.DigestDocument,
        body: str,
        *,
        chat_id: str = "",
        parts: int = 1,
        trigger: str = "cli",
    ) -> DigestDelivery:
        delivery = DigestDelivery(
            sent_at=to_utc_iso(utc_now()) or "",
            chat_id=chat_id,
            title=doc.title,
            parts=parts,
            items_count=len(doc.cards),
            body=body,
            trigger=trigger,
        )
        self.db.deliveries.add(delivery, [(c.id, c.follow_up_of) for c in doc.cards])
        return delivery

    def send(self, doc: render.DigestDocument, *, trigger: str = "cli") -> DigestDelivery:
        """Отправить дайджест в чат и записать доставку. `DeliveryError` — не ушло."""
        body = self.render(doc)
        parts = self.bot.send_text(body)
        _, chat_id = self.bot.credentials()
        return self.record(doc, body, chat_id=chat_id, parts=parts, trigger=trigger)


def render_date(iso: str) -> str:
    """`2026-09-20T07:00:00+00:00` → `20.09.2026`."""
    return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}" if len(iso) >= 10 else iso


__all__ = ["DeliveryError", "DigestService", "render_date"]
