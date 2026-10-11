"""Hybrid retrieval: BM25 + embedding cosine fused with RRF, then hard filters (SPEC 4, ADR-001).

All recipes are scored by both methods (the corpus is small), the two rankings are fused
with reciprocal rank fusion, the three-valued filters of `constraints.py` are applied, and
the top `TOP_K` survivors form the retrieved set. If the query embedding fails, BM25 alone
is used and the mode says so (R-62). The embedding index must match the corpus (R-63).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np
from rank_bm25 import BM25Okapi

from recipe_qa.config import (
    BM25_B,
    BM25_K1,
    BM25_TITLE_BOOST,
    BM25_TOKEN_PATTERN,
    EMBED_BATCH,
    EMBED_DOC_MAX_CHARS,
    EMBED_TIMEOUT_S,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    OPENAI_API_BASE,
    RRF_K,
    TOP_K,
)
from recipe_qa.constraints import apply_filters
from recipe_qa.corpus import Corpus, Recipe
from recipe_qa.schemas import AppliedConstraints, RetrievalMode

log = logging.getLogger(__name__)


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
    return BM25_TOKEN_PATTERN.findall(text.lower())


def document_text(recipe: Recipe) -> str:
    """Text that represents a recipe for BM25 and for embeddings, bounded by EMBED_DOC_MAX_CHARS."""
    parts = [
        recipe.title,
        recipe.summary,
        ", ".join(recipe.categories),
        "\n".join(recipe.ingredients),
        recipe.method,
    ]
    return "\n\n".join(p for p in parts if p)[:EMBED_DOC_MAX_CHARS]


def rrf(rankings: list[list[str]], k: int) -> dict[str, float]:
    """Reciprocal rank fusion: sum over rankings of 1 / (k + rank), ranks start at 1."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, rid in enumerate(ranking, start=1):
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + rank)
    return scores


def _normalise(vectors: np.ndarray) -> np.ndarray:
    arr = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return np.asarray(arr / norms, dtype=np.float32)


class Index:
    """In-memory BM25 index plus optional unit vectors aligned with the corpus order."""

    def __init__(
        self, corpus: Corpus, vectors: np.ndarray | None, vector_ids: list[str] | None
    ) -> None:
        self.corpus = corpus
        self.recipes = list(corpus.recipes)
        self.by_id = corpus.by_id()
        docs = [
            tokenize(r.title) * BM25_TITLE_BOOST + tokenize(document_text(r)) for r in self.recipes
        ]
        self.bm25 = BM25Okapi(docs, k1=BM25_K1, b=BM25_B)
        self.vectors: np.ndarray | None = None
        if vectors is not None:
            if vector_ids is None or len(vector_ids) != len(vectors):
                raise IndexMismatchError("vector ids and vectors differ in length")
            position = {rid: i for i, rid in enumerate(vector_ids)}
            missing = [r.id for r in self.recipes if r.id not in position]
            if missing or len(position) != len(self.recipes):
                raise IndexMismatchError(
                    f"index ids do not match the corpus (missing: {missing[:3]})"
                )
            self.vectors = _normalise(np.asarray(vectors)[[position[r.id] for r in self.recipes]])

    def bm25_ranking(self, question: str) -> list[tuple[str, float]]:
        scores = np.asarray(self.bm25.get_scores(tokenize(question)), dtype=np.float64)
        order = np.argsort(-scores, kind="stable")
        return [(self.recipes[i].id, float(scores[i])) for i in order]

    def vector_ranking(self, query_vector: np.ndarray) -> list[tuple[str, float]]:
        if self.vectors is None:
            raise IndexMismatchError("no vectors loaded")
        sims = self.vectors @ _normalise(query_vector)
        order = np.argsort(-sims, kind="stable")
        return [(self.recipes[i].id, float(sims[i])) for i in order]

    def retrieve(
        self, question: str, constraints: AppliedConstraints, embedder: Embedder | None
    ) -> RetrievalResult:
        bm25 = self.bm25_ranking(question)
        vec: list[tuple[str, float]] | None = None
        mode = RetrievalMode.BM25_ONLY
        if embedder is not None and self.vectors is not None:
            try:
                query_vector = embedder.embed([question])[0]
                vec = self.vector_ranking(query_vector)
                mode = RetrievalMode.HYBRID
            except Exception as exc:  # any embedding failure degrades, never breaks (R-62)
                log.warning("query embedding failed, falling back to BM25 only: %s", exc)
        rankings = [[rid for rid, _ in bm25]]
        if vec is not None:
            rankings.append([rid for rid, _ in vec])
        fused = rrf(rankings, RRF_K)
        bm25_pos = {rid: (i + 1, score) for i, (rid, score) in enumerate(bm25)}
        vec_pos = {rid: (i + 1, score) for i, (rid, score) in enumerate(vec or [])}
        ranked = sorted(
            (
                Candidate(
                    id=rid,
                    bm25_rank=bm25_pos[rid][0],
                    vec_rank=vec_pos[rid][0] if rid in vec_pos else None,
                    bm25_score=bm25_pos[rid][1],
                    cosine=vec_pos[rid][1] if rid in vec_pos else None,
                    rrf=score,
                )
                for rid, score in fused.items()
            ),
            key=lambda c: (-c.rrf, c.bm25_rank or 0),
        )
        ordered = [self.by_id[c.id] for c in ranked]
        survivors = apply_filters(ordered, constraints)
        return RetrievalResult(
            mode=mode,
            ranked=ranked,
            retrieved=survivors[:TOP_K],
            filtered_out=len(ordered) - len(survivors),
        )


def save_index(
    path: Path, *, vectors: np.ndarray, ids: list[str], model: str, corpus_version: str
) -> None:
    """SPEC 2.4: vectors plus the metadata needed to verify them at startup."""
    np.savez(
        path,
        vectors=np.asarray(vectors, dtype=np.float32),
        ids=np.asarray(ids),
        model=np.asarray(model),
        dimension=np.asarray(int(np.asarray(vectors).shape[1])),
        corpus_version=np.asarray(corpus_version),
    )


def load_index(path: Path, corpus: Corpus, *, model: str, dimension: int) -> Index:
    """Load the index file and refuse it if model, dimension or corpus version differ (R-63)."""
    with np.load(path, allow_pickle=False) as data:
        file_model = str(data["model"])
        file_dim = int(data["dimension"])
        file_version = str(data["corpus_version"])
        vectors = np.asarray(data["vectors"], dtype=np.float32)
        ids = [str(x) for x in data["ids"]]
    problems = []
    if file_model != model:
        problems.append(f"model {file_model!r} != {model!r}")
    if file_dim != dimension or vectors.shape[1] != dimension:
        problems.append(f"dimension {file_dim} != {dimension}")
    if file_version != corpus.corpus_version:
        problems.append(f"corpus_version {file_version} != {corpus.corpus_version}")
    if problems:
        raise IndexMismatchError("; ".join(problems))
    return Index(corpus, vectors, ids)


def build_vectors(corpus: Corpus, embedder: Embedder) -> tuple[np.ndarray, list[str]]:
    """Embed every recipe's document text in corpus order; rows are unit vectors."""
    texts = [document_text(r) for r in corpus.recipes]
    chunks = [embedder.embed(texts[i : i + EMBED_BATCH]) for i in range(0, len(texts), EMBED_BATCH)]
    vectors = _normalise(np.concatenate(chunks, axis=0)) if chunks else np.zeros((0, 0))
    return vectors, [r.id for r in corpus.recipes]


class OpenAIEmbedder:
    """`text-embedding-3-small` through the OpenAI REST API; the key never leaves the header."""

    model = EMBEDDING_MODEL
    dimension = EMBEDDING_DIM

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = EMBED_TIMEOUT_S,
        base_url: str = OPENAI_API_BASE,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )
        self.total_tokens = 0

    def embed(self, texts: list[str]) -> np.ndarray:
        response = self._client.post("/embeddings", json={"model": self.model, "input": texts})
        response.raise_for_status()
        payload = response.json()
        self.total_tokens += int(payload.get("usage", {}).get("total_tokens", 0))
        items = sorted(payload["data"], key=lambda d: int(d["index"]))
        vectors = np.asarray([item["embedding"] for item in items], dtype=np.float32)
        if vectors.shape != (len(texts), self.dimension):
            raise RuntimeError(f"unexpected embedding shape {vectors.shape}")
        return vectors
