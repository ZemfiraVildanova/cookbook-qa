"""Retrieval tests (SPEC 4, R-62, R-63, ADR-001). Fake embedder, no network."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from recipe_qa import retrieval
from recipe_qa.config import EMBED_DOC_MAX_CHARS, RRF_K, TOP_K
from recipe_qa.corpus import Corpus, compute_version
from recipe_qa.schemas import AppliedConstraints, Diet, RetrievalMode
from tests.fakes import FakeEmbedder, make_recipe

pytestmark = pytest.mark.xfail(strict=True, reason="implemented in next commit")


def corpus() -> Corpus:
    recipes = [
        make_recipe(
            "spaghetti-alla-carbonara",
            "Spaghetti alla Carbonara",
            minutes=60,
            ingredients=["450 g spaghetti", "5 egg yolks", "guanciale", "pecorino"],
            method="Boil the spaghetti. Fry the guanciale. Mix eggs and cheese. Toss.",
            vegetarian="no",
            vegan="no",
        ),
        make_recipe(
            "tomato-soup",
            "Tomato Soup",
            minutes=25,
            ingredients=["6 tomatoes", "1 onion", "vegetable stock"],
            method="Simmer tomatoes with onion in stock. Blend. Season.",
            vegetarian="yes",
            vegan="yes",
        ),
        make_recipe(
            "lentil-curry",
            "Lentil Curry",
            minutes=None,
            ingredients=["1 cup lentils", "coconut milk", "curry powder"],
            method="Cook lentils in coconut milk with spices. Mention carbonara never.",
            vegetarian="yes",
            vegan="yes",
        ),
        make_recipe(
            "pancakes",
            "Pancakes",
            minutes=15,
            ingredients=["2 cups flour", "2 eggs", "1 cup milk"],
            method="Whisk, pour, flip. A quick breakfast; some serve them like spaghetti.",
            vegetarian="yes",
            vegan="no",
        ),
        make_recipe(
            "walnut-cake",
            "Walnut Cake",
            minutes=90,
            ingredients=["200 g walnuts", "4 eggs", "150 g sugar"],
            method="Grind walnuts, fold into batter, bake.",
            vegetarian="yes",
            vegan="no",
        ),
    ]
    dumped = [r.model_dump() for r in recipes]
    return Corpus(
        corpus_version=compute_version(dumped),
        source="test",
        license="test",
        built_at="2026-01-01T00:00:00+00:00",
        recipes=recipes,
    )


def build_index(embedder: FakeEmbedder | None = None) -> retrieval.Index:
    c = corpus()
    vectors, ids = retrieval.build_vectors(c, embedder or FakeEmbedder())
    return retrieval.Index(c, vectors, ids)


def test_tokenize() -> None:
    """BM25_TOKEN_PATTERN: lower-case alphanumeric runs."""
    assert retrieval.tokenize("Spaghetti alla Carbonara, 450 g!") == [
        "spaghetti",
        "alla",
        "carbonara",
        "450",
        "g",
    ]


def test_document_text_contains_fields_and_is_bounded() -> None:
    """SPEC 8: EMBED_DOC_MAX_CHARS bounds the embedded text."""
    r = make_recipe("long", "Long Recipe", method="x " * 10_000, ingredients=["salt"])
    text = retrieval.document_text(r)
    assert text.startswith("Long Recipe")
    assert "salt" in text
    assert len(text) <= EMBED_DOC_MAX_CHARS


def test_rrf_formula_and_order() -> None:
    """RRF: score = sum over rankings of 1 / (k + rank), rank starting at 1."""
    scores = retrieval.rrf([["a", "b", "c"], ["b", "a"]], k=RRF_K)
    assert scores["a"] == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 2))
    assert scores["b"] == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))
    assert scores["c"] == pytest.approx(1 / (RRF_K + 3))
    assert sorted(scores, key=scores.get, reverse=True)[-1] == "c"  # type: ignore[arg-type]


def test_bm25_title_boost() -> None:
    """ADR-001: a title match outranks a single mention in another recipe's method."""
    index = build_index()
    ranking = index.bm25_ranking("spaghetti")
    assert ranking[0][0] == "spaghetti-alla-carbonara"
    assert ranking[0][1] > ranking[1][1] > 0
    ids = [rid for rid, _ in ranking]
    assert "pancakes" in ids  # the method mention still scores


def test_hybrid_retrieve_top_k_and_scores() -> None:
    """SPEC 4: hybrid mode, at most TOP_K, full ranking with both ranks for the log."""
    index = build_index()
    result = index.retrieve("how do I make carbonara", AppliedConstraints(), FakeEmbedder())
    assert result.mode is RetrievalMode.HYBRID
    assert result.retrieved[0].id == "spaghetti-alla-carbonara"
    assert len(result.retrieved) <= TOP_K
    assert len(result.ranked) == 5
    top = result.ranked[0]
    assert top.id == "spaghetti-alla-carbonara"
    assert top.bm25_rank == 1 and top.vec_rank is not None
    assert top.cosine is not None and -1.0 <= top.cosine <= 1.0
    assert result.filtered_out == 0


def test_filters_apply_after_fusion() -> None:
    """SPEC 4 step 4: hard filters remove candidates; filtered_out counts them."""
    index = build_index()
    result = index.retrieve("something quick", AppliedConstraints(max_minutes=30), FakeEmbedder())
    ids = {r.id for r in result.retrieved}
    assert ids <= {"tomato-soup", "pancakes"}
    assert result.filtered_out == 3  # carbonara 60, walnut cake 90, lentil curry unknown
    vegan = index.retrieve("dinner", AppliedConstraints(diet=Diet.VEGAN), FakeEmbedder())
    assert {r.id for r in vegan.retrieved} == {"tomato-soup", "lentil-curry"}


def test_filters_can_leave_nothing() -> None:
    """R-19 input: an impossible combination leaves an empty retrieved set."""
    index = build_index()
    result = index.retrieve(
        "anything", AppliedConstraints(diet=Diet.VEGAN, max_minutes=5), FakeEmbedder()
    )
    assert result.retrieved == []
    assert result.filtered_out == 5


def test_bm25_fallback() -> None:
    """R-62: embedding failure degrades to BM25 only and still returns results."""
    index = build_index()
    result = index.retrieve("tomato soup", AppliedConstraints(), FakeEmbedder(fail=True))
    assert result.mode is RetrievalMode.BM25_ONLY
    assert result.retrieved[0].id == "tomato-soup"
    assert all(c.vec_rank is None and c.cosine is None for c in result.ranked)


def test_no_embedder_means_bm25_only() -> None:
    index = build_index()
    result = index.retrieve("tomato soup", AppliedConstraints(), None)
    assert result.mode is RetrievalMode.BM25_ONLY


def test_vectors_are_normalised_and_aligned() -> None:
    c = corpus()
    vectors, ids = retrieval.build_vectors(c, FakeEmbedder())
    assert vectors.shape == (5, FakeEmbedder.dimension)
    assert ids == [r.id for r in c.recipes]
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)


def test_save_and_load_index_roundtrip(tmp_path: Path) -> None:
    """SPEC 2.4: the index file carries model, dimension and corpus_version."""
    c = corpus()
    vectors, ids = retrieval.build_vectors(c, FakeEmbedder())
    path = tmp_path / "embeddings.npz"
    retrieval.save_index(
        path, vectors=vectors, ids=ids, model="fake-embedder", corpus_version=c.corpus_version
    )
    index = retrieval.load_index(path, c, model="fake-embedder", dimension=64)
    result = index.retrieve("walnut cake", AppliedConstraints(), FakeEmbedder())
    assert result.retrieved[0].id == "walnut-cake"


def test_index_hash_mismatch_fails(tmp_path: Path) -> None:
    """R-63: an index built for another corpus version is refused."""
    c = corpus()
    vectors, ids = retrieval.build_vectors(c, FakeEmbedder())
    path = tmp_path / "embeddings.npz"
    retrieval.save_index(
        path, vectors=vectors, ids=ids, model="fake-embedder", corpus_version="deadbeefcafe"
    )
    with pytest.raises(retrieval.IndexMismatchError):
        retrieval.load_index(path, c, model="fake-embedder", dimension=64)


def test_index_model_or_dimension_mismatch_fails(tmp_path: Path) -> None:
    c = corpus()
    vectors, ids = retrieval.build_vectors(c, FakeEmbedder())
    path = tmp_path / "embeddings.npz"
    retrieval.save_index(
        path, vectors=vectors, ids=ids, model="fake-embedder", corpus_version=c.corpus_version
    )
    with pytest.raises(retrieval.IndexMismatchError):
        retrieval.load_index(path, c, model="other-model", dimension=64)
    with pytest.raises(retrieval.IndexMismatchError):
        retrieval.load_index(path, c, model="fake-embedder", dimension=1536)


def test_retrieval_is_deterministic() -> None:
    index = build_index()
    a = index.retrieve("eggs and cheese", AppliedConstraints(), FakeEmbedder())
    b = index.retrieve("eggs and cheese", AppliedConstraints(), FakeEmbedder())
    assert [r.id for r in a.retrieved] == [r.id for r in b.retrieved]
    assert [c.rrf for c in a.ranked] == [c.rrf for c in b.ranked]
