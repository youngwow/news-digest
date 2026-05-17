"""Tests for dedup_stories() in merge_chunks.py."""

from merge_chunks import dedup_stories


def _stories():
    """Two pairs of mergeable stories plus one standalone."""
    return [
        {
            "title": "Россия атаковала Украину массированным ударом дронов",
            "category": "политика", "importance": 8,
            "sources": ["src1"], "urls": ["http://a.com"],
        },
        {
            "title": "Россия атаковала Украину массированным ударом ракет и дронов",
            "category": "политика", "importance": 7,
            "sources": ["src2"], "urls": ["http://b.com"],
        },
        {
            "title": "Госдума разрешила Путину привлекать военных",
            "category": "политика", "importance": 6,
            "sources": ["src3"], "urls": ["http://c.com"],
        },
        {
            "title": "Госдума разрешила Путину привлекать военных для защиты россиян",
            "category": "политика", "importance": 6,
            "sources": ["src4"], "urls": ["http://d.com"],
        },
        {
            "title": "Запуск новой ракеты-носителя с космодрома Восточный",
            "category": "наука", "importance": 5,
            "sources": ["src5"], "urls": ["http://e.com"],
        },
    ]


def test_dedup_collapses_five_inputs_to_three_groups():
    assert len(dedup_stories(_stories())) == 3


def test_canonical_title_is_the_longer_one():
    result = dedup_stories(_stories())
    drone = next(s for s in result if "src1" in s["sources"])
    duma = next(s for s in result if "src3" in s["sources"])
    assert drone["title"] == "Россия атаковала Украину массированным ударом ракет и дронов"
    assert duma["title"] == "Госдума разрешила Путину привлекать военных для защиты россиян"


def test_importance_is_max_of_merged_stories():
    result = dedup_stories(_stories())
    drone = next(s for s in result if "src1" in s["sources"])
    assert drone["importance"] == 8


def test_sources_and_urls_are_merged_and_deduplicated():
    result = dedup_stories(_stories())
    drone = next(s for s in result if "src1" in s["sources"])
    assert drone["sources"] == ["src1", "src2"]
    assert drone["urls"] == ["http://a.com", "http://b.com"]


def test_standalone_story_passes_through_unchanged():
    result = dedup_stories(_stories())
    standalone = next(s for s in result if "src5" in s["sources"])
    assert standalone["title"] == "Запуск новой ракеты-носителя с космодрома Восточный"
    assert standalone["category"] == "наука"


def test_empty_input_returns_empty_list():
    assert dedup_stories([]) == []
