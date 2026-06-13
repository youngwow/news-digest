"""Filesystem locations for the project, resolved once and injectable for tests."""

from __future__ import annotations

import os
from dataclasses import dataclass

# news_digest/paths.py → news_digest/ → project root
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_ROOT = os.path.dirname(_PKG_DIR)


@dataclass(frozen=True)
class ProjectPaths:
    """Absolute paths the pipeline reads/writes.

    Defaults derive from the package location (works for an editable install run
    from the repo). `NEWS_DIGEST_ROOT` overrides the root; tests construct an
    instance pointing at a tmp dir instead of monkeypatching a global.
    """

    root: str
    data_dir: str
    prompts_dir: str
    config_path: str
    sources_path: str
    env_path: str

    @classmethod
    def from_root(cls, root: str | None = None) -> "ProjectPaths":
        root = root or os.environ.get("NEWS_DIGEST_ROOT") or _DEFAULT_ROOT
        return cls(
            root=root,
            data_dir=os.path.join(root, "data"),
            prompts_dir=os.path.join(root, "prompts"),
            config_path=os.path.join(root, "config.yaml"),
            sources_path=os.path.join(root, "sources.json"),
            env_path=os.path.join(root, ".env"),
        )

    def data(self, *parts: str) -> str:
        """Join a path under data/ (e.g. paths.data('digest.json'))."""
        return os.path.join(self.data_dir, *parts)


DEFAULT_PATHS = ProjectPaths.from_root()
