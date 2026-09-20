"""Рубрики из config.yaml → categories: один словарь для схемы ответа модели,
промпта, фильтра ленты и дайджеста."""

from __future__ import annotations

import pytest
from support import NEWS_TEXT, FakeLLM, news_answer

from src.config import DEFAULT_CATEGORIES, CategoriesConfig, Config, ConfigError
from src.processing import prompts
from src.processing.llm import extract_json
from src.processing.pipeline import Pipeline
from src.processing.schema import InvalidResponse, parse_result, result_schema
from src.services.feed_service import FeedService

# ── конфиг ─────────────────────────────────────────────────────────────────


def test_categories_section_is_optional_and_defaults_to_the_built_in_vocabulary(raw_config):
    del raw_config["categories"]
    cats = Config.from_dict(raw_config).categories
    assert cats.order == list(DEFAULT_CATEGORIES)
    assert cats.heading_for("ии") == "🤖 ИИ и данные"
    assert cats.heading_for("политика") == "🏛️ Политика"
    assert cats.fallback == "прочее"


def test_unknown_category_gets_a_neutral_emoji_and_a_capitalised_label():
    cats = CategoriesConfig(order=["x"], emoji={}, labels={})
    assert cats.heading_for("нечто") == "📌 Нечто"


@pytest.mark.parametrize(
    "order, message",
    [
        ([], "non-empty list"),
        (["a", "a"], "duplicates"),
        (["a", ""], "non-empty strings"),
        ("ии, финансы", "non-empty list"),
    ],
)
def test_bad_category_lists_are_rejected(raw_config, order, message):
    raw_config["categories"]["order"] = order
    with pytest.raises(ConfigError, match=message):
        Config.from_dict(raw_config)


def test_emoji_and_labels_must_be_mappings(raw_config):
    raw_config["categories"]["emoji"] = ["🤖"]
    with pytest.raises(ConfigError, match="mappings"):
        Config.from_dict(raw_config)


# ── схема и разбор ответа ──────────────────────────────────────────────────


def test_result_schema_carries_the_vocabulary_as_the_tag_enum():
    schema = result_schema(["ии", "финансы"])
    assert schema["properties"]["tags"]["items"]["enum"] == ["ии", "финансы"]
    # the module-level template is left untouched
    assert "enum" not in result_schema()["properties"]["tags"]["items"]


def test_parse_result_drops_tags_outside_the_vocabulary_and_keeps_order():
    answer = news_answer(tags=["финансы", "выдумка", "ии", "финансы"])
    result = parse_result(answer, tags=["ии", "финансы"])
    assert result.tags == ["финансы", "ии"]


def test_parse_result_without_a_vocabulary_keeps_any_non_empty_tag():
    result = parse_result(news_answer(tags=["что угодно", " ", "ещё"]))
    assert result.tags == ["что угодно", "ещё"]


def test_parse_result_still_rejects_a_bad_priority(raw_config):
    with pytest.raises(InvalidResponse, match="priority"):
        parse_result(news_answer(priority="urgent"), tags=["ии"])


# ── промпт и пайплайн ──────────────────────────────────────────────────────


def test_system_prompt_lists_the_vocabulary_and_the_enum():
    system = prompts.system_prompt(["ии", "петербург"])
    assert "рубрики только из списка: ии, петербург" in system
    enum_block = system[system.index('"tags": {') :]
    assert enum_block.index('"ии"') < enum_block.index('"петербург"')


def test_system_prompt_without_vocabulary_asks_for_an_empty_list():
    assert "рубрики не заданы" in prompts.system_prompt()


def test_pipeline_sends_the_vocabulary_and_filters_the_answer(config):
    provider = FakeLLM(news_answer(tags=["конкуренты", "не-рубрика"]))
    pipeline = Pipeline(config.processing, config.llm, provider, tags=config.categories.order)

    draft = pipeline.process(NEWS_TEXT, title="Заголовок")

    assert draft.tags == ["конкуренты"]
    assert provider.schemas[0]["properties"]["tags"]["items"]["enum"] == config.categories.order
    assert "конкуренты" in provider.systems[0]


def test_feed_filters_fall_back_to_the_configured_vocabulary(config, db):
    assert FeedService(config, db).filters()["tags"] == config.categories.order


# ── почти-JSON от облачной модели ──────────────────────────────────────────


def test_extract_json_repairs_a_trailing_comma_and_a_missing_brace():
    raw = 'Ответ:\n{"type": "news", "summary": ["a", "b", "c",], "priority": "high"'
    assert extract_json(raw) == {"type": "news", "summary": ["a", "b", "c"], "priority": "high"}


def test_extract_json_prefers_a_clean_object_over_repair():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json("") is None
    assert extract_json("no json here") is None
