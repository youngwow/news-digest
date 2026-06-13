"""Tests for news_digest.models — round-trip fidelity and Story.merge."""

from news_digest.models import Article, Digest, DigestDocument, Story

# ── Article ─────────────────────────────────────────────────────────

def test_article_roundtrip_without_body():
    d = {"title": "t", "url": "http://a", "published": "2026-06-10T10:00:00+00:00",
         "summary": "s", "source": "Meduza"}
    a = Article.from_dict(d)
    assert a.to_dict() == d  # body omitted when empty


def test_article_includes_body_when_present():
    a = Article(title="t", url="http://a", body="полный текст")
    assert a.to_dict()["body"] == "полный текст"


# ── Story ───────────────────────────────────────────────────────────

def test_story_roundtrip_minimal():
    d = {"title": "t", "category": "политика", "importance": 8,
         "sources": ["a"], "urls": ["http://a"]}
    assert Story.from_dict(d).to_dict() == d


def test_story_optional_fields_roundtrip():
    d = {"title": "t", "category": "мир", "importance": 7, "sources": ["a"],
         "urls": ["http://a"], "short_summary": "одно предложение",
         "follow_up_of": "вчерашний сюжет"}
    assert Story.from_dict(d).to_dict() == d


def test_story_omits_empty_optionals():
    s = Story(title="t", category="спорт", importance=5, sources=["a"], urls=["u"])
    assert "short_summary" not in s.to_dict()
    assert "follow_up_of" not in s.to_dict()


def test_story_merge_keeps_longer_and_max():
    a = Story(title="короткий", short_summary="кратко", importance=6,
              sources=["s1"], urls=["u1"])
    b = Story(title="более длинный заголовок", short_summary="развёрнутое описание",
              importance=8, sources=["s2"], urls=["u2", "u1"])
    a.merge(b)
    assert a.title == "более длинный заголовок"
    assert a.short_summary == "развёрнутое описание"
    assert a.importance == 8
    assert a.sources == ["s1", "s2"]
    assert a.urls == ["u1", "u2"]  # union, order-preserving, deduped


def test_story_copy_is_independent():
    a = Story(title="t", sources=["s1"], urls=["u1"])
    b = a.copy()
    b.sources.append("s2")
    assert a.sources == ["s1"]


# ── Digest / DigestDocument ─────────────────────────────────────────

def test_digest_document_roundtrip_preserves_schema():
    doc = {
        "generated_at": "2026-06-11T07:25:24+00:00",
        "source_file": "data/raw_news.json",
        "total_raw": 36,
        "total_unique": 32,
        "digest": {
            "headline": "Заголовок",
            "date": "11.06.2026",
            "top5": [
                {"title": "A", "summary": "суть", "url": "http://a"},
                {"title": "B", "thread": True},
            ],
            "rubrics": {"политика": [{"title": "X"}, {"title": "Y", "thread": True}]},
            "rest": ["c1", "c2"],
        },
        "all_stories": [
            {"title": "A", "category": "политика", "importance": 9,
             "sources": ["s"], "urls": ["http://a"]},
        ],
    }
    assert DigestDocument.from_dict(doc).to_dict() == doc


def test_digest_from_dict_tolerates_old_snapshot_without_summaries():
    """Older snapshots have titles-only top5 — must still load."""
    d = {"headline": "H", "date": "01.01.2026",
         "top5": [{"title": "A"}], "rubrics": {}, "rest": []}
    digest = Digest.from_dict(d)
    assert digest.top5[0].summary is None
    assert digest.to_dict()["top5"] == [{"title": "A"}]
