"""Render a DigestDocument to Telegram text or Markdown."""

from __future__ import annotations

from datetime import datetime

from ..config import CategoriesConfig
from ..models import DigestDocument
from ..text import pluralize_ru

REST_LIMIT_TELEGRAM = 25
REST_LIMIT_MARKDOWN = 50


def _stories_word(n) -> str:
    return pluralize_ru(n, "сюжет", "сюжета", "сюжетов") if isinstance(n, int) else "сюжетов"


def _articles_word(n) -> str:
    return pluralize_ru(n, "статья", "статьи", "статей") if isinstance(n, int) else "статей"


class TelegramRenderer:
    """Compact, plain-text digest for Telegram (auto-links URLs)."""

    def __init__(self, categories: CategoriesConfig):
        self.categories = categories

    def render(self, doc: DigestDocument) -> str:
        digest = doc.digest
        lines: list[str] = []
        date_str = digest.date or "сегодня"
        lines.append(f"📰 Дайджест новостей — {date_str}")
        lines.append("")

        if digest.headline:
            lines.append(f"🔥 {digest.headline}")
            lines.append("")

        if digest.top5:
            lines.append("▸▸▸ Главное ▸▸▸")
            for i, item in enumerate(digest.top5[:5], 1):
                mark = "🔄 " if item.thread else ""
                lines.append(f"{i}. {mark}{item.title}")
                if item.summary:
                    lines.append(f"   {item.summary}")
                if item.url:
                    lines.append(f"   {item.url}")
            lines.append("")

        if digest.rubrics:
            lines.append("▸▸▸ По темам ▸▸▸")
            for cat, items in digest.rubrics.items():
                lines.append(f"🔹 {self.categories.label_for(cat)}")
                for item in items[:5]:
                    mark = "🔄 " if item.thread else ""
                    lines.append(f"→ {mark}{item.title}")
                lines.append("")

        if digest.rest:
            lines.append("▸▸▸ Также в новостях ▸▸▸")
            for title in digest.rest[:REST_LIMIT_TELEGRAM]:
                lines.append(f"• {title}")
            lines.append("")

        total, total_raw = doc.total_unique, doc.total_raw
        lines.append(f"— {total} {_stories_word(total)} / {total_raw} {_articles_word(total_raw)} "
                     f"/ {date_str}")
        return "\n".join(lines)


class MarkdownRenderer:
    """Full Markdown digest document (data/digest.md)."""

    def __init__(self, categories: CategoriesConfig):
        self.categories = categories

    def render(self, doc: DigestDocument) -> str:
        digest = doc.digest
        lines = [
            f"# 📰 Дайджест новостей — {digest.date}",
            "",
            "## 🔥 Заголовок дня",
            f"**{digest.headline}**",
            "",
            "---",
            "## 🏆 Топ-5 главных новостей",
            "",
        ]
        for i, item in enumerate(digest.top5, 1):
            mark = "🔄 " if item.thread else ""
            lines.append(f"### {i}. {mark}{item.title}")
            if item.summary:
                lines.append(item.summary)
            if item.url:
                lines.append(f"<{item.url}>")
            lines.append("")

        lines += ["---", "", "## 📂 Рубрики", ""]
        for cat in self.categories.order:
            if cat in digest.rubrics:
                lines.append(f"### {self.categories.emoji_for(cat)} {cat.capitalize()}")
                lines.append("")
                for item in digest.rubrics[cat]:
                    mark = "🔄 " if item.thread else ""
                    lines.append(f"- {mark}{item.title}")
                lines.append("")

        lines += ["---", "", "## 📋 Остальные новости кратко", ""]
        for t in digest.rest[:REST_LIMIT_MARKDOWN]:
            lines.append(f"- {t}")

        total, total_raw = doc.total_unique, doc.total_raw
        lines += [
            "",
            "---",
            f"*Сгенерировано AI: {datetime.now().strftime('%d.%m.%Y %H:%M')} MSK*",
            f"*Проанализировано: {total} {_stories_word(total)} / "
            f"{total_raw} {_articles_word(total_raw)}*",
        ]
        return "\n".join(lines)


def split_message(text: str, limit: int) -> list[str]:
    """Split text into Telegram-sized parts on block boundaries (\\n\\n) where possible."""
    blocks = text.split("\n\n")
    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            parts.append("\n\n".join(current))
            current = []
            current_len = 0

    for block in blocks:
        block_len = len(block) + (2 if current else 0)
        if block_len > limit:
            flush()
            parts.extend(_split_oversized_block(block, limit))
            continue
        if current and current_len + block_len > limit:
            flush()
            block_len = len(block)
        current.append(block)
        current_len += block_len
    flush()
    return parts


def _split_oversized_block(block: str, limit: int) -> list[str]:
    """Line-level split for a single block that exceeds the limit."""
    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in block.split("\n"):
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            parts.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        parts.append("\n".join(current))
    return parts
