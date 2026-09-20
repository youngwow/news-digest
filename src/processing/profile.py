"""The reader profile: whose point of view decides `priority`.

A profile is a prompt fragment — a company, a team or one person's interests —
so it is versioned rather than edited in place: a card always says which
version judged it (`items.profile_version`). The built-in one below matches the
default source pool (sources.json); `profile set <file.json>` replaces it.
"""

from __future__ import annotations

from ..models import CompanyProfile

DEFAULT_NAME = "Читатель: ИИ и данные, разработка, финансы, Петербург"

# Интересы читателя по умолчанию — под пул каналов в sources.json (Хабр, Data Secrets,
# AI-новости, Т—Ж, Investnique, Фонтанка). Ключи те же, что у профиля компании:
# промпт и фронтенд читают их одинаково.
DEFAULT_PAYLOAD: dict = {
    "industry": (
        "личный новостной мониторинг: искусственный интеллект и данные, разработка ПО, "
        "инвестиции и личные финансы, жизнь Петербурга"
    ),
    "products": [],
    "stack": ["Python", "машинное обучение и LLM", "данные и аналитика"],
    "regime": "",
    "regulators": ["Банк России", "Минцифры", "Роскомнадзор", "ФНС"],
    "competitors": [],
    "topics": [
        "большие языковые модели и агенты: релизы, исследования, инструменты",
        "машинное обучение, данные и инфраструктура для них",
        "разработка: языки, фреймворки, инженерная практика, карьера в ИТ",
        "инвестиции, рынки, ставки, налоги и личные финансы",
        "экономика и регулирование ИТ в России",
        "Петербург: город, транспорт, события, происшествия с последствиями",
    ],
    "negative_facets": [
        "реклама, промокоды, розыгрыши и партнёрские подборки",
        "спорт",
        "шоу-бизнес и светская хроника",
        "гороскопы и развлекательные тесты",
    ],
}


def ensure_default(db) -> CompanyProfile:
    """Seed the built-in profile once; later runs reuse whatever is stored."""
    existing = db.profiles.default()
    if existing is not None:
        return existing
    profile = db.profiles.save(
        CompanyProfile(name=DEFAULT_NAME, payload=DEFAULT_PAYLOAD, is_default=True)
    )
    db.profiles.set_default(profile.id)
    profile.is_default = True
    return profile


def resolve(db, profile_id: int | None = None) -> CompanyProfile:
    """The profile a run judges by: the requested one, else the default."""
    if profile_id is not None:
        profile = db.profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"профиль #{profile_id} не найден")
        return profile
    return ensure_default(db)
