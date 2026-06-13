"""DigestBuilder: pure transform from a story list to a Digest."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ..config import CategoriesConfig
from ..models import Digest, RubricEntry, Story, Top5Entry

RUBRIC_LIMIT = 5
TOP5_LIMIT = 5


def _format_date(classified_at: str) -> str:
    """Render the run date in local time (so a post-midnight run stamps today)."""
    try:
        dt = datetime.fromisoformat(classified_at.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d.%m.%Y")
    except (ValueError, TypeError, AttributeError):
        return datetime.now().astimezone().strftime("%d.%m.%Y")


class DigestBuilder:
    def __init__(self, categories: CategoriesConfig):
        self.categories = categories

    def build(self, stories: list[Story], classified_at: str = "") -> Digest:
        """Sort by importance (desc, longer title breaks ties) → headline/top5/rubrics/rest.

        Sorts `stories` in place. Requires a non-empty list.
        """
        stories.sort(key=lambda s: (s.importance, len(s.title)), reverse=True)

        top5 = stories[:TOP5_LIMIT]
        top5_titles = {s.title.strip().lower() for s in top5}

        by_cat: dict[str, list[Story]] = defaultdict(list)
        for s in stories:
            by_cat[s.category].append(s)

        rubrics: dict[str, list[RubricEntry]] = {}
        for cat in self.categories.order:
            items = [s for s in by_cat.get(cat, [])
                     if s.title.strip().lower() not in top5_titles][:RUBRIC_LIMIT]
            if items:
                rubrics[cat] = [RubricEntry(s.title, thread=bool(s.follow_up_of)) for s in items]

        used = set(top5_titles)
        for items in rubrics.values():
            used.update(e.title.strip().lower() for e in items)

        rest: list[str] = []
        for s in stories[TOP5_LIMIT:]:
            t = s.title.strip()
            if t.lower() not in used:
                rest.append(t)
                used.add(t.lower())

        top5_entries = [
            Top5Entry(
                title=s.title,
                summary=s.short_summary or None,
                url=(s.urls[0] if s.urls else None),
                thread=bool(s.follow_up_of),
            )
            for s in top5
        ]

        return Digest(
            headline=top5[0].title,
            date=_format_date(classified_at),
            top5=top5_entries,
            rubrics=rubrics,
            rest=rest,
        )
