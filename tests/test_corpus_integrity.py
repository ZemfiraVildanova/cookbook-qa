"""Committed corpus checks (SPEC 2.1 quotas, 2.4 versioning). Pure file checks, no network."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

from recipe_qa.corpus import Corpus, compute_version, load_corpus

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "corpus.json"
LOCK = ROOT / "data" / "corpus.lock.json"
MANIFEST = ROOT / "data" / "manifest.yaml"


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return load_corpus(CORPUS)


@pytest.fixture(scope="module")
def manifest() -> dict[str, object]:
    data: dict[str, object] = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return data


def test_version_matches_content(corpus: Corpus) -> None:
    """SPEC 2.4: corpus_version is the hash of the recipes array."""
    assert corpus.corpus_version == compute_version([r.model_dump() for r in corpus.recipes])
    assert len(corpus.corpus_version) == 12


def test_size_within_spec(corpus: Corpus) -> None:
    """SPEC 2.1: 50 ± 5 recipes."""
    assert 45 <= len(corpus.recipes) <= 55


def test_ids_unique_and_well_formed(corpus: Corpus) -> None:
    ids = [r.id for r in corpus.recipes]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)
    for r in corpus.recipes:
        assert r.url.startswith("https://en.wikibooks.org/wiki/Cookbook:")
        assert r.title and not r.title.startswith("Cookbook:")
        assert r.method, f"{r.id} has no method text"
        assert r.ingredients, f"{r.id} has no ingredient lines"


def test_manifest_and_lock_agree_with_corpus(corpus: Corpus, manifest: dict[str, object]) -> None:
    """SPEC 2.4: every manifest page is in the lock and in the corpus at the locked revision."""
    pages = manifest["pages"]
    assert isinstance(pages, list)
    titles = [p["title"] for p in pages]
    assert len(titles) == len(set(titles))
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert set(lock) == set(titles)
    by_page_id = {r.page_id: r for r in corpus.recipes}
    assert len(by_page_id) == len(titles)
    for entry in lock.values():
        recipe = by_page_id[entry["page_id"]]
        assert recipe.revision_id == entry["revision_id"]


def test_variety_quotas(corpus: Corpus, manifest: dict[str, object]) -> None:
    """SPEC 2.1 quotas: pairs, structure, diet and time coverage."""
    ids = {r.id for r in corpus.recipes}
    pairs = manifest["pairs"]
    assert isinstance(pairs, list)
    assert len(pairs) >= 3
    for pair in pairs:
        assert len(pair) >= 2
        for recipe_id in pair:
            assert recipe_id in ids, f"pair member {recipe_id} is not in the corpus"
    assert sum(not r.has_summary_template for r in corpus.recipes) >= 2
    assert sum(r.total_minutes is None for r in corpus.recipes) >= 8
    assert sum("yes" in r.diet.values() for r in corpus.recipes) >= 10
    assert sum(r.total_minutes is not None for r in corpus.recipes) >= 30


def test_groups_cover_required_categories(manifest: dict[str, object]) -> None:
    """SPEC 2.1: soups, desserts, vegetarian dishes, pasta and noodles, national cuisines."""
    pages = manifest["pages"]
    assert isinstance(pages, list)
    groups = Counter(p["group"] for p in pages)
    for required in ("soups", "desserts", "vegetarian", "pasta"):
        assert groups[required] >= 5, f"group {required} has {groups[required]} pages"
    cuisines = [g for g in groups if g not in {"soups", "desserts", "vegetarian", "pasta"}]
    assert len(cuisines) >= 1
