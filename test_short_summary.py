#!/usr/bin/env python3
"""
Tests for make_short_summary() — word-boundary truncation fix.

Run: python3 -m pytest test_short_summary.py -v
Or:  python3 test_short_summary.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_full import make_short_summary


def test_sentence_boundary_with_space():
    """Sentence ends with '. ' — cut at full sentence if long enough. If too short, word fallback."""
    result = make_short_summary(
        "Some Title",
        "Первое предложение закончено. Второе предложение начинается здесь и продолжается очень долго с множеством слов которые идут дальше и дальше по тексту занимая всё больше места."
    )
    # First sentence boundary is at 28 chars (< 40 guard), so falls through to
    # word-boundary fallback which gives us a longer chunk ending with "…"
    assert "…" in result, f"Expected word-boundary truncation, got: {result}"
    last_word = result.rstrip("…").rstrip().split()[-1]
    assert len(last_word) >= 2, f"Mid-word cut: '{last_word}' in '{result}'"


def test_word_boundary_not_mid_word():
    """Long summary near MAX_LEN — should not cut in the middle of a word."""
    result = make_short_summary(
        "Military Parade",
        "военный парад посвящённый празднованию дня победы прошёл на красной площади с участием тысяч военнослужащих и сотен единиц боевой техники а также с пролётом авиации над москвой"
    )
    # Should NOT end with mid-word fragment like "п", "по"
    last_word = result.rstrip("…").rstrip(".").split()[-1]
    assert len(last_word) >= 2, f"Mid-word cut: last token='{last_word}' in '{result}'"
    # Should end with "…" indicating truncation at a word boundary
    assert "…" in result, f"Expected ellipsis in truncated result: {result}"


def test_sentence_boundary_not_mid_word():
    """Sentence boundary should only match when next char is space/EOS, not mid-word."""
    # A summary where a period appears mid-word (abbreviation or mid-word dot)
    # The old code could match "." inside a word as a sentence end
    result = make_short_summary(
        "Tech News",
        "Компания Apple Inc. анонсировала новый продукт который изменит рынок технологий и принесёт революцию в индустрию мобильных устройств по всему миру включая развивающиеся страны."
    )
    # "Inc." should NOT be treated as a sentence boundary
    # The result should cut at a real boundary or word boundary, not after "Inc."
    assert not result.rstrip("…").rstrip().endswith("Inc."), f"Wrongly cut at abbreviation: {result}"


def test_long_sentence_no_boundary_falls_back_to_word():
    """A single long sentence with no punctuation — falls back to word boundary."""
    result = make_short_summary(
        "Some Title",
        "очень длинное предложение без знаков препинания которое продолжается и продолжается бесконечно чтобы проверить что код правильно обрезает по пробелу а не по середине слова"
    )
    last_word = result.rstrip("…").split()[-1]
    assert len(last_word) >= 2, f"Mid-word cut in long sentence: '{last_word}' in '{result}'"


def test_min_length_guard_falls_back_to_title():
    """If summary is too short and truncation would produce < 30 chars, use title."""
    # Summary so short that even the sentence boundary is too close to start
    result = make_short_summary(
        "Полный заголовок новости который достаточно длинный",
        "Кратко."
    )
    # "Кратко." is < 30 chars, so should fall back to title
    assert result != "Кратко.", f"Should have fallen back to title, got: {result}"
    assert "заголовок" in result.lower(), f"Expected title content in result: {result}"


def test_short_sentence_boundary_under_40_falls_through():
    """Sentence boundary found but < 40 chars — should use word-boundary fallback for longer chunk."""
    result = make_short_summary(
        "Some Title",
        "Коротко. А это уже более длинное продолжение которое должно дать нам нормальный кусок текста для отображения в дайджесте чтобы не было слишком коротко и обрезано."
    )
    # "Коротко." is < 40 chars, so it should NOT return just that
    # It should fall through to word-boundary and give us more text
    assert len(result) > 40, f"Result too short — didn't fall through: '{result}'"


def test_title_without_summary():
    """No summary — uses title directly."""
    result = make_short_summary("Заголовок новости", "")
    assert result == "Заголовок новости", f"Got: {result}"


def test_none_summary():
    """None summary — uses title."""
    # The function is called with get("summary", "") from line 172, so None
    # won't reach it in practice, but test behavior if it did.
    try:
        result = make_short_summary("Some Title", None)  # type: ignore[arg-type]
        assert result == "Some Title", f"Got: {result}"
    except (TypeError, AttributeError):
        pass  # ok if it errors on None — caller guards against it


def test_very_short_summary():
    """Summary <= 20 chars — falls back to title."""
    result = make_short_summary("Полный заголовок", "Коротко")
    assert result == "Полный заголовок", f"Got: {result}"


def test_real_world_russian_example_1():
    """Real bug: 'военный парад по' was cut mid-word."""
    result = make_short_summary(
        "Парад на Красной площади",
        "военный парад посвящённый семьдесят второй годовщине победы в великой отечественной войне состоялся на красной площади в москве с участием более десяти тысяч военнослужащих"
    )
    # Should NOT end with "по "
    last_word = result.rstrip("…").rstrip(".").rstrip().split()[-1]
    assert last_word not in ("п", "по", "пос"), f"Mid-word cut: '{last_word}' in '{result}'"


def test_real_world_russian_example_2():
    """Real bug: 'создания яд' was cut mid-word."""
    result = make_short_summary(
        "Ядерная программа",
        "создания ядерного оружия продолжается несмотря на международные санкции и давление со стороны западных стран которые требуют прекращения программы"
    )
    last_word = result.rstrip("…").rstrip(".").rstrip().split()[-1]
    assert last_word not in ("яд", "яде"), f"Mid-word cut: '{last_word}' in '{result}'"


def test_real_world_russian_example_3():
    """Real bug: 'военное ведо' was cut mid-word."""
    result = make_short_summary(
        "Военное ведомство",
        "военное ведомство сообщило о завершении испытаний нового гиперзвукового комплекса который способен преодолевать любые системы противоракетной обороны потенциального противника"
    )
    last_word = result.rstrip("…").rstrip(".").rstrip().split()[-1]
    assert last_word not in ("вед", "ведо"), f"Mid-word cut: '{last_word}' in '{result}'"


if __name__ == "__main__":
    # Simple runner when pytest is not available
    tests = [obj for name, obj in globals().items() if name.startswith("test_") and callable(obj)]
    passed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {test.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {test.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
