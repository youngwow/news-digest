"""Typed configuration loaded from config.yaml.

Replaces the old module-global CONFIG dict + _REQUIRED_SCHEMA validation with
nested dataclasses. `Config.load()` parses and validates; services receive the
slice they need (e.g. `Scraper(config.scraper, ...)`) instead of importing a
global, which makes them trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from .paths import DEFAULT_PATHS


class ConfigError(Exception):
    """Raised when config.yaml is missing required keys or is malformed."""


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    timeout: float
    max_retries: int
    api_key_env: str | None = None


@dataclass(frozen=True)
class LLMConfig:
    active: str
    classify_concurrency: int
    cache_retention_days: int
    failure_tolerance: float
    bad_json_retry: bool
    providers: list[Provider]

    @property
    def active_provider(self) -> Provider:
        for p in self.providers:
            if p.name == self.active:
                return p
        raise ConfigError(
            f"llm.active '{self.active}' does not match any provider "
            f"(available: {[p.name for p in self.providers]})"
        )


@dataclass(frozen=True)
class ScraperConfig:
    date_window_hours: float
    request_timeout: float
    max_redirects: int
    user_agent: str
    fetch_bodies: bool
    body_max_chars: int
    body_timeout: float
    body_concurrency: int
    body_cache_retention_days: int


@dataclass(frozen=True)
class PipelineConfig:
    chunk_size: int
    training_log: bool


@dataclass(frozen=True)
class TelegramConfig:
    message_limit: int
    enabled: bool
    alerts: bool


@dataclass(frozen=True)
class HealthConfig:
    min_sources_ok: int
    expected_sources: int
    default_max_age_minutes: int


@dataclass(frozen=True)
class DedupConfig:
    overlap_threshold: float
    shared_words_min: int
    containment_min_len: int
    cross_run_enabled: bool
    cross_run_retention_days: int
    semantic_enabled: bool
    semantic_threshold: float
    semantic_model: str
    semantic_model_static: str


@dataclass(frozen=True)
class ArchiveConfig:
    retention_days: int


@dataclass(frozen=True)
class ThreadsConfig:
    enabled: bool
    lookback_days: int
    similarity_threshold: float


@dataclass(frozen=True)
class HeuristicsConfig:
    multi_source_max_boost: int
    recency_window_hours: float
    recency_boost: int
    source_weight_max_boost: int


@dataclass(frozen=True)
class CategoriesConfig:
    order: list[str]
    emoji: dict[str, str]
    labels: dict[str, str]

    def emoji_for(self, category: str) -> str:
        return self.emoji.get(category, "📌")

    def label_for(self, category: str) -> str:
        return self.labels.get(category, f"📌 {category}")


_SECTIONS = {
    "llm": LLMConfig, "scraper": ScraperConfig, "pipeline": PipelineConfig,
    "telegram": TelegramConfig, "health": HealthConfig, "dedup": DedupConfig,
    "archive": ArchiveConfig, "threads": ThreadsConfig,
    "heuristics": HeuristicsConfig, "categories": CategoriesConfig,
}


def _build_section(name: str, cls: type, raw: dict):
    section = raw.get(name)
    if not isinstance(section, dict):
        raise ConfigError(f"config.yaml: missing or non-mapping section '{name}'")
    fields = cls.__dataclass_fields__
    missing = [k for k in fields if k not in section and k != "providers"]
    if name == "llm":
        missing = [k for k in fields if k not in section]
    if missing:
        raise ConfigError(f"config.yaml: section '{name}' missing keys: {missing}")
    try:
        if name == "llm":
            providers = [Provider(**p) for p in section["providers"]]
            kwargs = {k: section[k] for k in fields if k != "providers"}
            return cls(providers=providers, **kwargs)
        return cls(**{k: section[k] for k in fields})
    except TypeError as e:
        raise ConfigError(f"config.yaml: section '{name}' is malformed: {e}") from e


@dataclass(frozen=True)
class Config:
    llm: LLMConfig
    scraper: ScraperConfig
    pipeline: PipelineConfig
    telegram: TelegramConfig
    health: HealthConfig
    dedup: DedupConfig
    archive: ArchiveConfig
    threads: ThreadsConfig
    heuristics: HeuristicsConfig
    categories: CategoriesConfig
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: dict) -> "Config":
        if not isinstance(raw, dict):
            raise ConfigError("config.yaml: top level must be a mapping")
        sections = {name: _build_section(name, cls_, raw) for name, cls_ in _SECTIONS.items()}
        cfg = cls(raw=raw, **sections)
        cfg.validate()
        return cfg

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        path = path or DEFAULT_PATHS.config_path
        try:
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except FileNotFoundError as e:
            raise ConfigError(f"config.yaml not found at {path}") from e
        return cls.from_dict(raw)

    def validate(self) -> None:
        if not self.llm.providers:
            raise ConfigError("config.yaml: llm.providers must contain at least one entry")
        self.llm.active_provider  # raises if active name is unknown
        for cat in self.categories.order:
            if cat not in self.categories.emoji:
                raise ConfigError(f"config.yaml: categories.emoji missing entry for '{cat}'")
            if cat not in self.categories.labels:
                raise ConfigError(f"config.yaml: categories.labels missing entry for '{cat}'")
