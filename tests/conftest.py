"""Shared pytest fixtures for the news_digest suite."""

import pytest
from support import valid_raw_config

from news_digest.config import Config


@pytest.fixture
def raw_config() -> dict:
    return valid_raw_config()


@pytest.fixture
def config() -> Config:
    return Config.from_dict(valid_raw_config())
