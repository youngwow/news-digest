"""Рендер дайджеста: срез ленты в текст для руководителя (markdown) или для
Telegram-чата (компактный, с ссылками, режется под лимит сообщения).

Группировка по приоритету и `high` сверху — прямое следствие задачи «утренний
разбор»: читают сверху вниз и останавливаются, когда важное кончилось. Telegram-
вариант добавляет к этому структуру «главное → рубрики → остальное» и пометку 🔄
у карточек, продолжающих уже доставленную историю.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field

from ..config import CategoriesConfig, DigestConfig
from ..models import PRIORITIES

TITLES = {"high": "Высокий приоритет", "medium": "Средний приоритет", "low": "Низкий приоритет"}
TYPE_TITLES = {"npa": "НПА", "news": "Новость"}
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


# ── markdown: срез по приоритетам ──────────────────────────────────────────


def group_by_priority(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """`high` → `medium` → `low`; внутри группы порядок приходит уже отсортированным."""
    groups = {p: [] for p in PRIORITIES}
    for row in rows:
        groups.setdefault(row.get("priority", "low"), []).append(row)
    return [(p, groups[p]) for p in PRIORITIES if groups.get(p)]


def to_markdown(rows: list[dict], *, title: str, generated_at: str, notes: dict | None = None) -> str:
    """Дайджест в markdown: заголовок, группы по приоритету, карточки со ссылками."""
    lines = [f"# {title}", "", f"_Сформировано: {generated_at}; материалов: {len(rows)}_", ""]
    for priority, group in group_by_priority(rows):
        lines.append(f"## {TITLES.get(priority, priority)} ({len(group)})")
        lines.append("")
        for row in group:
            kind = TYPE_TITLES.get(row.get("type"), row.get("type", ""))
            status = f", {row['npa_status']}" if row.get("npa_status") else ""
            lines.append(f"### {row.get('title') or 'Без заголовка'}")
            lines.append(f"*{kind}{status}* · {(row.get('published_at') or '')[:10]}"
                         + (f" · {row['source_name']}" if row.get("source_name") else ""))
            lines.append("")
            for sentence in (row.get("summary") or "").split("\n"):
                if sentence.strip():
                    lines.append(sentence.strip())
            if row.get("tags"):
                lines.append("")
                lines.append("Теги: " + ", ".join(row["tags"]))
            if row.get("canonical_url"):
                lines.append("")
                lines.append(f"[Оригинал]({row['canonical_url']})")
            for note in (notes or {}).get(row["id"], []):
                lines.append("")
                lines.append(f"> Заметка: {note}")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


# ── telegram: главное → рубрики → остальное ────────────────────────────────


@dataclass
class DigestCard:
    """Карточка так, как её видит дайджест: без служебных полей ленты."""

    id: int
    title: str
    url: str | None
    lead: str  # первое предложение саммари — подпись под заголовком в «Главном»
    priority: str
    category: str
    source: str
    published_at: str
    follow_up_of: int | None = None  # ранее доставленная карточка той же истории (🔄)

    @classmethod
    def from_row(cls, row: dict, category: str) -> "DigestCard":
        summary = row.get("summary") or ""
        lead = next((s.strip() for s in summary.split("\n") if s.strip()), "")
        if lead == (row.get("title") or "").strip():
            lead = ""  # заголовок и есть первое предложение — не повторять
        return cls(
            id=int(row["id"]),
            title=(row.get("title") or "Без заголовка").strip(),
            url=row.get("canonical_url") or None,
            lead=lead,
            priority=row.get("priority") or "low",
            category=category,
            source=row.get("source_name") or "",
            published_at=row.get("published_at") or "",
        )


@dataclass
class DigestDocument:
    """Собранный дайджест: главное, рубрики, остальное. Рендер — `to_telegram`."""

    title: str
    generated_at: str
    top: list[DigestCard] = field(default_factory=list)
    rubrics: list[tuple[str, list[DigestCard]]] = field(default_factory=list)
    rest: list[DigestCard] = field(default_factory=list)
    total: int = 0
    sources: int = 0

    @property
    def headline(self) -> DigestCard | None:
        return self.top[0] if self.top else None

    @property
    def cards(self) -> list[DigestCard]:
        """Все карточки документа в порядке появления — их и запоминает доставка."""
        seen: dict[int, DigestCard] = {}
        for card in self.top + [c for _, cards in self.rubrics for c in cards] + self.rest:
            seen.setdefault(card.id, card)
        return list(seen.values())

    @property
    def is_empty(self) -> bool:
        return not self.top


def category_of(row: dict, categories: CategoriesConfig) -> str:
    """Рубрика карточки — первый её тег из словаря; без такого — последняя рубрика."""
    for tag in row.get("tags") or []:
        if tag in categories.order:
            return tag
    return categories.fallback


def compose(
    rows: list[dict],
    *,
    categories: CategoriesConfig,
    settings: DigestConfig,
    title: str,
    generated_at: str,
    threads: dict[int, int] | None = None,
) -> DigestDocument:
    """Разложить срез ленты по разделам дайджеста.

    «Главное» — первые `settings.top` карточек по приоритету, релевантности и
    свежести; остальное группируется по рубрикам (не больше `per_category` в каждой,
    в порядке словаря), что не поместилось — «также в новостях» одной строкой.
    """
    # Свежие раньше, затем устойчивая сортировка по приоритету и релевантности:
    # при равных оценках наверху оказывается более новая карточка.
    ordered = sorted(rows, key=lambda r: r.get("published_at") or "", reverse=True)
    ordered.sort(
        key=lambda r: (
            _PRIORITY_RANK.get(r.get("priority"), 3),
            -float(r.get("relevance_score") or 0),
        )
    )
    threads = threads or {}
    cards = [DigestCard.from_row(r, category_of(r, categories)) for r in ordered]
    for card in cards:
        card.follow_up_of = threads.get(card.id)

    top = cards[: settings.top]
    by_category: dict[str, list[DigestCard]] = {}
    for card in cards[settings.top :]:
        by_category.setdefault(card.category, []).append(card)
    rubrics: list[tuple[str, list[DigestCard]]] = []
    rest: list[DigestCard] = []
    for category in categories.order:
        group = by_category.pop(category, [])
        if not group:
            continue
        rubrics.append((category, group[: settings.per_category]))
        rest.extend(group[settings.per_category :])
    for group in by_category.values():  # теги вне словаря — такого не бывает, но не терять
        rest.extend(group)
    return DigestDocument(
        title=title,
        generated_at=generated_at,
        top=top,
        rubrics=rubrics,
        rest=rest[: settings.rest],
        total=len(cards),
        sources=len({c.source for c in cards if c.source}),
    )


def _escape(text: str) -> str:
    """Telegram HTML: экранируются только `<`, `>` и `&`."""
    return html.escape(text or "", quote=False)


def _link(card: DigestCard) -> str:
    title = _escape(card.title)
    return f'<a href="{_escape(card.url)}">{title}</a>' if card.url else title


def _mark(card: DigestCard) -> str:
    return "🔄 " if card.follow_up_of else ""


def to_telegram(doc: DigestDocument, categories: CategoriesConfig) -> str:
    """Компактный текст для Telegram (parse_mode=HTML)."""
    lines: list[str] = [f"📰 <b>{_escape(doc.title)}</b>", ""]
    if doc.headline:
        lines.append(f"🔥 {_mark(doc.headline)}{_link(doc.headline)}")
        lines.append("")
    if doc.top:
        lines.append("▸▸▸ Главное ▸▸▸")
        for index, card in enumerate(doc.top, 1):
            lines.append(f"{index}. {_mark(card)}{_link(card)}")
            if card.lead:
                lines.append(f"   {_escape(card.lead)}")
        lines.append("")
    if doc.rubrics:
        lines.append("▸▸▸ По рубрикам ▸▸▸")
        for category, cards in doc.rubrics:
            lines.append(f"<b>{_escape(categories.heading_for(category))}</b>")
            for card in cards:
                lines.append(f"→ {_mark(card)}{_link(card)}")
            lines.append("")
    if doc.rest:
        lines.append("▸▸▸ Также в новостях ▸▸▸")
        for card in doc.rest:
            lines.append(f"• {_mark(card)}{_escape(card.title)}")
        lines.append("")
    lines.append(
        f"— {doc.total} {pluralize_ru(doc.total, 'карточка', 'карточки', 'карточек')} "
        f"из {doc.sources} {pluralize_ru(doc.sources, 'источника', 'источников', 'источников')} "
        f"· {doc.generated_at[:16].replace('T', ' ')} UTC"
    )
    return "\n".join(lines)


def pluralize_ru(n: int, one: str, few: str, many: str) -> str:
    """Форма для числа: 1 карточка / 2 карточки / 5 карточек."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many
