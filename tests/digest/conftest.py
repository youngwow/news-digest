"""Fixtures of the news_digest suite (tests/digest/).

The platform suite (tests/unit/) has its own conftest at tests/; the `config` and
`raw_config` fixtures defined here shadow its namesakes for this package only.
"""

import pytest

from digest.support import valid_raw_config
from news_digest.config import Config


@pytest.fixture
def raw_config() -> dict:
    return valid_raw_config()


@pytest.fixture
def config() -> Config:
    return Config.from_dict(valid_raw_config())
