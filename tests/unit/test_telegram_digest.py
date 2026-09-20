"""Telegram-дайджест: сборка «главное → рубрики → остальное», рендер под Bot API,
резка под лимит, доставка с записью и пометка продолжений (🔄)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from support import MockRoutes

from src.config import CategoriesConfig, Config, DigestConfig
from src.delivery.telegram import DeliveryError, TelegramBot, split_message
from src.models import DigestDelivery
from src.models.queries import FeedQuery
from src.processing.dedup import encode_vector
from src.repositories import Database
from src.repositories.feed import where
from src.services import digest as render
from src.services.digest_service import DigestService, render_date
from src.services.feed_service import FeedService

CATS = CategoriesConfig(
    order=["ии", "финансы", "прочее"],
    emoji={"ии": "🤖", "финансы": "📈", "прочее": "📌"},
    labels={"ии": "ИИ и данные"},
)
SETTINGS = DigestConfig(top=2, per_category=2, rest=3)
SEND_URL = "https://api.telegram.org/bottoken/sendMessage"


def _row(id: int, **overrides) -> dict:
    base = {
        "id": id,
        "title": f"Заголовок {id}",
        "summary": f"Первое предложение {id}.\nВторое.",
        "priority": "medium",
        "relevance_score": 0.5,
        "tags": [],
        "canonical_url": f"https://a.ru/{id}",
        "source_name": "Источник",
        "published_at": f"2026-09-{id:02d}T09:00:00+00:00",
    }
    base.update(overrides)
    return base


# ── compose ────────────────────────────────────────────────────────────────


def test_top_is_by_priority_then_relevance_then_freshness():
    rows = [
        _row(1, priority="low", relevance_score=0.9),
        _row(2, priority="high", relevance_score=0.3),
        _row(3, priority="high", relevance_score=0.8),
        _row(4, priority="medium", relevance_score=0.8),
        _row(5, priority="medium", relevance_score=0.8),  # свежее 4 при равных оценках
    ]
    doc = render.compose(rows, categories=CATS, settings=DigestConfig(top=3), title="t", generated_at="")
    assert [c.id for c in doc.top] == [3, 2, 5]
    assert doc.headline.id == 3
    assert doc.total == 5


def test_rubrics_follow_the_vocabulary_order_and_overflow_goes_to_rest():
    rows = [
        _row(1, priority="high"),
        _row(2, priority="high"),
        _row(3, tags=["финансы"]),
        _row(4, tags=["ии", "финансы"]),  # первый тег из словаря — рубрика
        _row(5, tags=["ии"]),
        _row(6, tags=["ии"]),  # третья в рубрике ии при per_category=2 → остальное
        _row(7, tags=["не-рубрика"]),  # тег вне словаря → последняя рубрика
        _row(8),
        _row(9),
        _row(10),
    ]
    doc = render.compose(rows, categories=CATS, settings=SETTINGS, title="t", generated_at="")
    assert {c.id for c in doc.top} == {1, 2}  # оба high; при равных оценках свежее (2) первым
    assert [c.id for c in doc.top] == [2, 1]
    assert [(cat, [c.id for c in cards]) for cat, cards in doc.rubrics] == [
        ("ии", [6, 5]),  # свежие первыми, третья карточка рубрики (4) уходит в остальное
        ("финансы", [3]),
        ("прочее", [10, 9]),
    ]
    assert [c.id for c in doc.rest] == [4, 8, 7]  # rest=3: из четырёх переполнивших остаются три
    assert len(doc.cards) == 2 + 5 + 3


def test_category_of_prefers_the_first_vocabulary_tag():
    assert render.category_of({"tags": ["x", "финансы", "ии"]}, CATS) == "финансы"
    assert render.category_of({"tags": []}, CATS) == "прочее"


def test_lead_is_dropped_when_it_repeats_the_title():
    card = render.DigestCard.from_row(_row(1, title="Одно", summary="Одно\nДва"), "ии")
    assert card.lead == ""
    assert render.DigestCard.from_row(_row(1, summary=" Первое. \nВторое."), "ии").lead == "Первое."


def test_empty_slice_gives_an_empty_document():
    doc = render.compose([], categories=CATS, settings=SETTINGS, title="t", generated_at="")
    assert doc.is_empty and doc.cards == [] and doc.headline is None


# ── render ─────────────────────────────────────────────────────────────────


def test_telegram_text_escapes_html_links_cards_and_marks_threads():
    rows = [
        _row(1, priority="high", title="A <b> & B", canonical_url="https://a.ru/?x=1&y=2"),
        _row(2, tags=["ии"], canonical_url=None),
        _row(3, tags=["ии"]),
        _row(4),
    ]
    doc = render.compose(
        rows, categories=CATS, settings=DigestConfig(top=1, per_category=5, rest=5),
        title="Дайджест 20.09.2026", generated_at="2026-09-20T07:00:00+00:00", threads={3: 99},
    )
    text = render.to_telegram(doc, CATS)
    assert text.startswith("📰 <b>Дайджест 20.09.2026</b>\n\n🔥 <a href=\"https://a.ru/?x=1&amp;y=2\">A &lt;b&gt; &amp; B</a>")
    assert "▸▸▸ Главное ▸▸▸\n1. <a href=" in text
    assert "   Первое предложение 1." in text
    assert "<b>🤖 ИИ и данные</b>\n→ 🔄 <a href=\"https://a.ru/3\">Заголовок 3</a>\n→ Заголовок 2" in text
    assert "<b>📌 Прочее</b>\n→ <a href=\"https://a.ru/4\">Заголовок 4</a>" in text
    assert text.endswith("— 4 карточки из 1 источника · 2026-09-20 07:00 UTC")


def test_heading_does_not_double_an_emoji_already_in_the_label():
    cats = CategoriesConfig(order=["x"], emoji={"x": "📌"}, labels={"x": "📌 Прочее"})
    assert cats.heading_for("x") == "📌 Прочее"


@pytest.mark.parametrize("n, word", [(1, "карточка"), (3, "карточки"), (11, "карточек"), (22, "карточки"), (100, "карточек")])
def test_pluralize(n, word):
    assert render.pluralize_ru(n, "карточка", "карточки", "карточек") == word


def test_render_date():
    assert render_date("2026-09-20T07:00:00+00:00") == "20.09.2026"


# ── split ──────────────────────────────────────────────────────────────────


def test_split_keeps_short_text_whole_and_cuts_on_block_boundaries():
    assert split_message("a\n\nb", 100) == ["a\n\nb"]
    blocks = ["x" * 40, "y" * 40, "z" * 40]
    parts = split_message("\n\n".join(blocks), 90)
    assert parts == ["x" * 40 + "\n\n" + "y" * 40, "z" * 40]
    assert all(len(p) <= 90 for p in parts)


def test_split_cuts_an_oversized_block_by_lines():
    block = "\n".join("l" * 30 for _ in range(5))
    parts = split_message(block, 65)
    assert len(parts) == 3 and all(len(p) <= 65 for p in parts)
    assert "\n".join(parts) == block


# ── bot ────────────────────────────────────────────────────────────────────


def _bot(routes, monkeypatch, config, **kwargs) -> tuple[TelegramBot, MockRoutes]:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    table = routes if isinstance(routes, MockRoutes) else MockRoutes(routes)
    bot = TelegramBot(
        config.telegram, transport=httpx.MockTransport(table.handler), sleep=lambda s: None, **kwargs
    )
    return bot, table


def test_bot_sends_one_message_with_html(monkeypatch, config):
    bot, table = _bot({SEND_URL: (200, b'{"ok": true}', {})}, monkeypatch, config)
    assert bot.send_text("<b>hi</b>") == 1
    body = json.loads(table.requests[0].content)
    assert body == {"chat_id": "42", "text": "<b>hi</b>", "disable_web_page_preview": True, "parse_mode": "HTML"}


def test_bot_numbers_the_parts_of_a_long_digest(monkeypatch, config):
    from dataclasses import replace

    bot, table = _bot({SEND_URL: (200, b"{}", {})}, monkeypatch, config)
    bot.config = replace(config.telegram, message_limit=120)
    assert bot.send_text("\n\n".join("x" * 50 for _ in range(4))) == 2
    texts = [json.loads(r.content)["text"] for r in table.requests]
    assert texts[0].startswith("[1/2]\n") and texts[1].startswith("[2/2]\n")


def test_bot_retries_429_with_the_requested_pause_then_succeeds(monkeypatch, config):
    replies = iter([
        (429, b'{"parameters": {"retry_after": 7}}', {}),
        (500, b"boom", {}),
        (200, b"{}", {}),
    ])
    pauses: list[float] = []
    bot, table = _bot({SEND_URL: lambda request: next(replies)}, monkeypatch, config)
    bot._sleep = pauses.append
    assert bot.send_text("x") == 1
    assert len(table.requests) == 3 and pauses == [7.0, 4]


def test_bot_fails_fast_on_a_client_error_and_after_exhausted_retries(monkeypatch, config):
    bot, _ = _bot({SEND_URL: (400, b'{"description": "chat not found"}', {})}, monkeypatch, config)
    with pytest.raises(DeliveryError, match="HTTP 400"):
        bot.send_text("x")
    bot, table = _bot({SEND_URL: (503, b"", {})}, monkeypatch, config)
    with pytest.raises(DeliveryError, match="после 3 попыток"):
        bot.send_text("x")
    assert len(table.requests) == 3


def test_bot_without_credentials_is_not_configured_and_refuses_to_send(config, tmp_path):
    bot = TelegramBot(config.telegram, env_path=str(tmp_path / ".env"))
    assert not bot.configured
    with pytest.raises(DeliveryError, match="TELEGRAM_BOT_TOKEN"):
        bot.send_text("x")


def test_bot_reads_credentials_from_dot_env(config, tmp_path):
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID='7'\n", encoding="utf-8")
    assert TelegramBot(config.telegram, env_path=str(tmp_path / ".env")).credentials() == ("t", "7")


def test_alert_is_sent_once_until_its_text_changes(monkeypatch, config, tmp_path):
    bot, table = _bot({SEND_URL: (200, b"{}", {})}, monkeypatch, config)
    hash_path = str(tmp_path / ".last_alert_hash")
    assert bot.send_alert("ALERT: a", hash_path) is True
    assert bot.send_alert("ALERT: a", hash_path) is False
    assert bot.send_alert("ALERT: b", hash_path) is True
    assert bot.send_alert("   ", hash_path) is False
    assert len(table.requests) == 2
    assert "parse_mode" not in json.loads(table.requests[0].content)


# ── repository + query ─────────────────────────────────────────────────────


def test_delivery_repo_records_lists_and_finds_recent_items(db, item_factory):
    doc_ids = _documents(db, 3)
    items = [item_factory(db, d) for d in doc_ids]
    first = DigestDelivery(sent_at="2026-09-01T07:00:00+00:00", title="1")
    db.deliveries.add(first, [(items[0], None), (items[1], items[0])])
    second = DigestDelivery(sent_at="2026-09-03T07:00:00+00:00", chat_id="42", title="2", parts=2)
    db.deliveries.add(second, [(items[2], None)])

    assert first.id == 1 and db.deliveries.get(1).title == "1"
    assert db.deliveries.last().id == 2
    assert [d.id for d in db.deliveries.list()] == [2, 1]
    assert db.deliveries.items(1) == [(items[0], None), (items[1], items[0])]
    assert db.deliveries.delivered_since("2026-09-02T00:00:00+00:00") == [items[2]]
    assert db.deliveries.is_delivered(items[1]) and not db.deliveries.is_delivered(999)


def test_undelivered_query_excludes_cards_already_in_a_digest(config, file_db, item_factory):
    doc_ids = _documents(file_db, 2)
    items = [item_factory(file_db, d) for d in doc_ids]
    file_db.deliveries.add(DigestDelivery(sent_at="2026-09-01T07:00:00+00:00"), [(items[0], None)])
    feed = FeedService(config, file_db)

    clauses, _ = where(FeedQuery.build(undelivered=True))
    assert any("digest_delivery_items" in c for c in clauses)
    everything = feed.visible_slice(FeedQuery.build())
    fresh = feed.visible_slice(FeedQuery.build(undelivered=True))
    assert {r["id"] for r in everything} == set(items)
    assert [r["id"] for r in fresh] == [items[1]]


def test_deleting_an_item_drops_its_delivery_rows(db, item_factory):
    (doc_id,) = _documents(db, 1)
    item_id = item_factory(db, doc_id)
    db.deliveries.add(DigestDelivery(sent_at="2026-09-01T07:00:00+00:00"), [(item_id, None)])
    db.conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.conn.commit()
    assert db.deliveries.items(1) == []


# ── service ────────────────────────────────────────────────────────────────


def _documents(db: Database, n: int, embeddings: dict[int, list[float]] | None = None) -> list[int]:
    from src.models import RawDocument, Source

    source = db.sources.get_by_fetch_url("https://a.ru/rss") or db.sources.add(
        Source(name="Лента", url="https://a.ru/", kind="rss", fetch_url="https://a.ru/rss")
    )
    ids = []
    with db.transaction():
        for index in range(1, n + 1):
            doc_id = db.documents.insert(
                RawDocument(
                    source_id=source.id,
                    external_id=f"d{index}-{len(ids)}-{db.conn.execute('SELECT count(*) FROM documents').fetchone()[0]}",
                    url=f"https://a.ru/{index}-{db.conn.execute('SELECT count(*) FROM documents').fetchone()[0]}",
                    title=f"Документ {index}",
                    text="Текст документа " * 5,
                    published_at=f"2026-09-{index:02d}T09:00:00+00:00",
                    fetched_at="2026-09-05T09:00:00+00:00",
                )
            )
            if embeddings and index in embeddings:
                db.documents.set_derived(doc_id, embedding=encode_vector(embeddings[index]))
            ids.append(doc_id)
    return ids


def _service(config: Config, db: Database, routes=None, monkeypatch=None) -> DigestService:
    bot = None
    if routes is not None:
        bot, _ = _bot(routes, monkeypatch, config)
    return DigestService(config, db, bot=bot)


def test_compose_undelivered_and_record_make_the_next_digest_skip_those_cards(config, file_db, item_factory):
    doc_ids = _documents(file_db, 3)
    items = [item_factory(file_db, d, priority="high") for d in doc_ids]
    service = _service(config, file_db)

    first = service.compose(undelivered=True)
    assert {c.id for c in first.cards} == set(items)
    delivery = service.record(first, service.render(first), trigger="run")
    assert (delivery.chat_id, delivery.items_count, delivery.trigger) == ("", 3, "run")
    assert file_db.deliveries.get(delivery.id).body.startswith("📰 <b>Дайджест")

    assert service.compose(undelivered=True).is_empty
    assert len(service.compose(undelivered=False).cards) == 3


def test_send_delivers_through_the_bot_and_records_chat_and_parts(config, file_db, item_factory, monkeypatch):
    (doc_id,) = _documents(file_db, 1)
    item_factory(file_db, doc_id)
    routes = MockRoutes({SEND_URL: (200, b"{}", {})})
    service = _service(config, file_db, routes, monkeypatch)

    delivery = service.send(service.compose(), trigger="cli")

    assert (delivery.chat_id, delivery.parts, delivery.items_count) == ("42", 1, 1)
    assert json.loads(routes.requests[0].content)["text"] == delivery.body
    assert file_db.deliveries.last().id == delivery.id


def test_send_failure_records_nothing(config, file_db, item_factory, monkeypatch):
    (doc_id,) = _documents(file_db, 1)
    item_factory(file_db, doc_id)
    service = _service(config, file_db, {SEND_URL: (403, b"forbidden", {})}, monkeypatch)
    with pytest.raises(DeliveryError):
        service.send(service.compose())
    assert file_db.deliveries.last() is None


def test_threads_link_a_card_to_a_recently_delivered_similar_one(config, file_db, item_factory, monkeypatch):
    import src.services.digest_service as service_mod

    now = datetime(2026, 9, 5, 7, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(service_mod, "utc_now", lambda: now)
    vectors = {1: [1.0, 0.0, 0.0], 2: [0.8, 0.6, 0.0], 3: [0.0, 0.0, 1.0], 4: [0.99, 0.14, 0.0]}
    doc_ids = _documents(file_db, 4, vectors)
    items = [item_factory(file_db, d) for d in doc_ids]
    old, unrelated = items[0], items[2]
    file_db.deliveries.add(
        DigestDelivery(sent_at=(now - timedelta(days=1)).isoformat()), [(old, None), (unrelated, None)]
    )
    service = _service(config, file_db)

    doc = service.compose(undelivered=True)  # карточки 2 и 4 ещё не доставлены

    by_id = {c.id: c for c in doc.cards}
    assert by_id[items[1]].follow_up_of == old  # cos(2,1) = 0.8 ≥ 0.75, ниже S1 0.86
    assert by_id[items[3]].follow_up_of == old  # cos(4,1) = 0.99 — тот же материал, тоже видел
    assert "🔄" in service.render(doc)


def test_threads_ignore_deliveries_outside_the_lookback_window_and_missing_vectors(config, file_db, item_factory, monkeypatch):
    import src.services.digest_service as service_mod

    now = datetime(2026, 9, 5, 7, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(service_mod, "utc_now", lambda: now)
    doc_ids = _documents(file_db, 2, {1: [1.0, 0.0], 2: [1.0, 0.0]})
    items = [item_factory(file_db, d) for d in doc_ids]
    file_db.deliveries.add(
        DigestDelivery(sent_at=(now - timedelta(days=10)).isoformat()), [(items[0], None)]
    )
    service = _service(config, file_db)
    assert service.threads(service.feed.visible_slice(FeedQuery.build(undelivered=True))) == {}

    from dataclasses import replace

    service.config = replace(config, digest=replace(config.digest, threads_enabled=False))
    assert all(c.follow_up_of is None for c in service.compose().cards)


def test_feed_service_digest_supports_the_telegram_format(config, file_db, item_factory):
    (doc_id,) = _documents(file_db, 1)
    item_factory(file_db, doc_id, title="Единственная")
    result = FeedService(config, file_db).digest(FeedQuery.build(), fmt="telegram", title="Проба")
    assert result["format"] == "telegram" and result["items"] == 1
    assert result["body"].startswith("📰 <b>Проба</b>") and "Единственная" in result["body"]


# ── CLI ────────────────────────────────────────────────────────────────────


def _parse(*argv):
    from src import cli

    return cli.build_parser().parse_args(list(argv))


def test_cli_digest_telegram_prints_and_does_not_record(config, hub_paths, file_db, item_factory, capsys):
    from src import cli

    (doc_id,) = _documents(file_db, 1)
    item_factory(file_db, doc_id, title="Карточка")
    assert cli._cmd_digest(_parse("digest", "--format", "telegram"), config, hub_paths) == 0
    out = capsys.readouterr().out
    assert out.startswith("📰 <b>Дайджест") and "Карточка" in out
    assert file_db.deliveries.last() is None


def test_cli_digest_record_then_undelivered_is_empty(config, hub_paths, file_db, item_factory, capsys):
    from src import cli

    (doc_id,) = _documents(file_db, 1)
    item_factory(file_db, doc_id)
    assert cli._cmd_digest(_parse("digest", "--format", "telegram", "--record"), config, hub_paths) == 0
    assert "записано как доставка #1" in capsys.readouterr().err
    assert cli._cmd_digest(_parse("digest", "--format", "telegram", "--undelivered", "--record"), config, hub_paths) == 0
    captured = capsys.readouterr()
    assert "нечего отправлять" in captured.err
    assert len(file_db.deliveries.list()) == 1


def test_cli_digest_send_uses_the_bot_and_deliveries_lists_it(config, hub_paths, file_db, item_factory, capsys, monkeypatch):
    from src import cli

    (doc_id,) = _documents(file_db, 1)
    item_factory(file_db, doc_id, title="Ушла в чат")
    routes = MockRoutes({SEND_URL: (200, b"{}", {})})
    bot, _ = _bot(routes, monkeypatch, config)
    monkeypatch.setattr(
        cli, "DigestService", lambda cfg, db, feed, env_path=None: DigestService(cfg, db, feed, bot=bot)
    )

    assert cli._cmd_digest(_parse("digest", "--send"), config, hub_paths) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and "отправлено: 1 карточек, 1 сообщение(й), доставка #1" in captured.err
    assert "Ушла в чат" in json.loads(routes.requests[0].content)["text"]

    assert cli._cmd_deliveries(_parse("deliveries"), config, hub_paths) == 0
    table = capsys.readouterr().out
    assert "чат 42" in table and "Дайджест" in table
    assert cli._cmd_deliveries(_parse("deliveries", "1"), config, hub_paths) == 0
    assert "Ушла в чат" in capsys.readouterr().out
    assert cli._cmd_deliveries(_parse("deliveries", "9"), config, hub_paths) == 1


def test_cli_digest_send_without_credentials_returns_1(config, hub_paths, file_db, item_factory, caplog):
    from src import cli

    (doc_id,) = _documents(file_db, 1)
    item_factory(file_db, doc_id)
    assert cli._cmd_digest(_parse("digest", "--send"), config, hub_paths) == 1
    assert "TELEGRAM_BOT_TOKEN" in caplog.text
    assert file_db.deliveries.last() is None


def test_cli_deliveries_when_nothing_was_sent(config, hub_paths, file_db, capsys):
    from src import cli

    assert cli._cmd_deliveries(_parse("deliveries"), config, hub_paths) == 0
    assert "ни разу" in capsys.readouterr().out
