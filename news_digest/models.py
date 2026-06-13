"""Domain models: Article, Story, Digest.

Services pass these around instead of raw dicts. JSON (de)serialization happens
only at the I/O boundary via from_dict/to_dict, which preserve the exact field
names used by the on-disk artifacts (raw_news.json, classified.json,
digest.json, archive snapshots) so existing data still loads and renders.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field


@dataclass
class Article:
    title: str = ""
    url: str = ""
    published: str | None = None
    summary: str = ""
    source: str = ""
    body: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Article":
        return cls(
            title=d.get("title", ""),
            url=d.get("url", ""),
            published=d.get("published"),
            summary=d.get("summary", ""),
            source=d.get("source", ""),
            body=d.get("body", "") or "",
        )

    def to_dict(self) -> dict:
        d = {
            "title": self.title,
            "url": self.url,
            "published": self.published,
            "summary": self.summary,
            "source": self.source,
        }
        if self.body:
            d["body"] = self.body
        return d


@dataclass
class Story:
    title: str = ""
    category: str = "прочее"
    importance: int = 5
    sources: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    short_summary: str = ""
    follow_up_of: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Story":
        return cls(
            title=d.get("title", ""),
            category=d.get("category", "прочее"),
            importance=int(d.get("importance", 5)) if isinstance(d.get("importance"), (int, float)) else d.get("importance", 5),
            sources=list(d.get("sources", [])),
            urls=list(d.get("urls", [])),
            short_summary=d.get("short_summary", "") or "",
            follow_up_of=d.get("follow_up_of"),
        )

    def to_dict(self) -> dict:
        d = {
            "title": self.title,
            "category": self.category,
            "importance": self.importance,
            "sources": self.sources,
            "urls": self.urls,
        }
        if self.short_summary:
            d["short_summary"] = self.short_summary
        if self.follow_up_of:
            d["follow_up_of"] = self.follow_up_of
        return d

    def copy(self) -> "Story":
        return dataclasses.replace(self, sources=list(self.sources), urls=list(self.urls))

    def merge(self, other: "Story") -> "Story":
        """Fold `other` into self (in place): union sources/urls, max importance,
        keep the longer title and summary."""
        self.sources = sorted(set(self.sources) | set(other.sources))
        self.urls = list(dict.fromkeys(self.urls + other.urls))
        self.importance = max(self.importance, other.importance)
        if len(other.title) > len(self.title):
            self.title = other.title
        if len(other.short_summary) > len(self.short_summary):
            self.short_summary = other.short_summary
        return self


@dataclass
class Top5Entry:
    title: str
    summary: str | None = None
    url: str | None = None
    thread: bool = False

    def to_dict(self) -> dict:
        d: dict = {"title": self.title}
        if self.thread:
            d["thread"] = True
        if self.summary:
            d["summary"] = self.summary
        if self.url:
            d["url"] = self.url
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Top5Entry":
        return cls(title=d.get("title", ""), summary=d.get("summary"),
                   url=d.get("url"), thread=bool(d.get("thread", False)))


@dataclass
class RubricEntry:
    title: str
    thread: bool = False

    def to_dict(self) -> dict:
        d: dict = {"title": self.title}
        if self.thread:
            d["thread"] = True
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RubricEntry":
        return cls(title=d.get("title", ""), thread=bool(d.get("thread", False)))


@dataclass
class Digest:
    headline: str = ""
    date: str = ""
    top5: list[Top5Entry] = field(default_factory=list)
    rubrics: dict[str, list[RubricEntry]] = field(default_factory=dict)
    rest: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "headline": self.headline,
            "date": self.date,
            "top5": [e.to_dict() for e in self.top5],
            "rubrics": {c: [e.to_dict() for e in items] for c, items in self.rubrics.items()},
            "rest": self.rest,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Digest":
        return cls(
            headline=d.get("headline", ""),
            date=d.get("date", ""),
            top5=[Top5Entry.from_dict(e) for e in d.get("top5", [])],
            rubrics={c: [RubricEntry.from_dict(e) for e in items]
                     for c, items in d.get("rubrics", {}).items()},
            rest=list(d.get("rest", [])),
        )


@dataclass
class DigestDocument:
    """The full digest.json wrapper: digest + provenance + all_stories."""

    digest: Digest
    all_stories: list[Story] = field(default_factory=list)
    generated_at: str = ""
    source_file: str = ""
    total_raw: int | str = 0
    total_unique: int = 0

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "source_file": self.source_file,
            "total_raw": self.total_raw,
            "total_unique": self.total_unique,
            "digest": self.digest.to_dict(),
            "all_stories": [s.to_dict() for s in self.all_stories],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DigestDocument":
        return cls(
            digest=Digest.from_dict(d.get("digest", {})),
            all_stories=[Story.from_dict(s) for s in d.get("all_stories", [])],
            generated_at=d.get("generated_at", ""),
            source_file=d.get("source_file", ""),
            total_raw=d.get("total_raw", 0),
            total_unique=d.get("total_unique", 0),
        )
