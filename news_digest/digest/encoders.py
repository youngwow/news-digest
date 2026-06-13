"""Sentence-embedding backends for semantic dedup and story threading.

Strategy pattern: an Encoder turns titles into vectors. load_encoder() returns
the first backend that imports cleanly (sentence-transformers on MPS/CUDA/CPU,
then torch-free model2vec), or None — callers then degrade gracefully.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Sequence

from ..config import DedupConfig
from ..jsonio import get_logger

log = get_logger("encoders")


def normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Dot product of unit-norm vectors."""
    return sum(x * y for x, y in zip(a, b))


class Encoder(ABC):
    @abstractmethod
    def encode(self, texts: list[str]) -> Sequence[Sequence[float]]:
        ...


class SentenceTransformerEncoder(Encoder):
    def __init__(self, model_name: str):
        import torch
        from sentence_transformers import SentenceTransformer
        self.device = ("mps" if torch.backends.mps.is_available()
                       else "cuda" if torch.cuda.is_available() else "cpu")
        self._model = SentenceTransformer(model_name, device=self.device)
        log.info("semantic backend: sentence-transformers (%s) on %s", model_name, self.device)

    def encode(self, texts: list[str]) -> Sequence[Sequence[float]]:
        return self._model.encode(texts, normalize_embeddings=True)


class Model2VecEncoder(Encoder):
    def __init__(self, model_name: str):
        from model2vec import StaticModel
        self._model = StaticModel.from_pretrained(model_name)
        log.info("semantic backend: model2vec static embeddings (%s)", model_name)

    def encode(self, texts: list[str]) -> Sequence[Sequence[float]]:
        return self._model.encode(texts)


def load_encoder(config: DedupConfig) -> Encoder | None:
    """First available backend, or None (→ word-overlap-only dedup)."""
    try:
        return SentenceTransformerEncoder(config.semantic_model)
    except Exception as e:  # noqa: BLE001 — ImportError, broken torch, download failure
        log.debug("sentence-transformers backend unavailable: %s", e)
    try:
        return Model2VecEncoder(config.semantic_model_static)
    except Exception as e:  # noqa: BLE001
        log.warning("semantic backend unavailable (%s); install with: pip install -e '.[ml]'", e)
        return None
