"""Tests for news_digest.text — extract_json, pluralize_ru."""

from news_digest.text import extract_json, pluralize_ru

# ── extract_json ────────────────────────────────────────────────

def test_extract_json_fenced_object():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_json_fenced_array():
    assert extract_json('```json\n[1, 2, 3]\n```') == '[1, 2, 3]'


def test_extract_json_unfenced_object():
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_with_leading_chatter():
    assert extract_json('Here is the result:\n{"stories": []}\nDone.') == '{"stories": []}'


def test_extract_json_picks_outermost_pair():
    assert extract_json('noise {"a": {"b": 1}} more noise') == '{"a": {"b": 1}}'


def test_extract_json_empty_input():
    assert extract_json("") == ""


def test_extract_json_no_json_returns_empty():
    assert extract_json("no json here, just plain text") == ""


def test_extract_json_unterminated_returns_empty():
    assert extract_json("[1, 2, 3, incomplete") == ""


def test_extract_json_picks_whichever_opens_first():
    assert extract_json('{"a": 1} then [2]') == '{"a": 1}'
    assert extract_json('[1, 2] then {"b": 3}') == '[1, 2]'


# ── pluralize_ru ────────────────────────────────────────────────

def test_pluralize_ru_forms():
    forms = ("сюжет", "сюжета", "сюжетов")
    assert pluralize_ru(1, *forms) == "сюжет"
    assert pluralize_ru(2, *forms) == "сюжета"
    assert pluralize_ru(4, *forms) == "сюжета"
    assert pluralize_ru(5, *forms) == "сюжетов"
    assert pluralize_ru(11, *forms) == "сюжетов"
    assert pluralize_ru(12, *forms) == "сюжетов"
    assert pluralize_ru(21, *forms) == "сюжет"
    assert pluralize_ru(22, *forms) == "сюжета"
    assert pluralize_ru(51, *forms) == "сюжет"
    assert pluralize_ru(100, *forms) == "сюжетов"
    assert pluralize_ru(111, *forms) == "сюжетов"
