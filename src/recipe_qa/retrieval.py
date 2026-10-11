"""Hybrid retrieval: BM25 + embedding cosine fused with RRF, then hard filters (SPEC 4, ADR-001).

Implemented in the next commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from recipe_qa.corpus import Corpus, Recipe
from recipe_qa.schemas import AppliedConstraints, RetrievalMode


class Embedder(Protocol):
    model: str
    dimension: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class IndexMismatchError(RuntimeError):
    """The embedding index does not belong to the loaded corpus (R-63)."""


@dataclass(frozen=True)
class Candidate:
    id: str
    bm25_rank: int | None
    vec_rank: int | None
    bm25_score: float
    cosine: float | None
    rrf: float


@dataclass(frozen=True)
class RetrievalResult:
    mode: RetrievalMode
    ranked: list[Candidate]
    retrieved: list[Recipe]
    filtered_out: int


def tokenize(text: str) -> list[str]:
    raise NotImplementedError


def document_text(recipe: Recipe) -> str:
    raise NotImplementedError


def rrf(rankings: list[list[str]], k: int) -> dict[str, float]:
    raise NotImplementedError


class Index:
    def __init__(
        self, corpus: Corpus, vectors: np.ndarray | None, vector_ids: list[str] | None
    ) -> None:
        raise NotImplementedError

    def bm25_ranking(self, question: str) -> list[tuple[str, float]]:
        raise NotImplementedError

    def vector_ranking(self, query_vector: np.ndarray) -> list[tuple[str, float]]:
        raise NotImplementedError

    def retrieve(
        self, question: str, constraints: AppliedConstraints, embedder: Embedder | None
    ) -> RetrievalResult:
        raise NotImplementedError


def save_index(
    path: Path, *, vectors: np.ndarray, ids: list[str], model: str, corpus_version: str
) -> None:
    raise NotImplementedError


def load_index(path: Path, corpus: Corpus, *, model: str, dimension: int) -> Index:
    raise NotImplementedError


def build_vectors(corpus: Corpus, embedder: Embedder) -> tuple[np.ndarray, list[str]]:
    raise NotImplementedError
