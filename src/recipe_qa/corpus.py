"""Corpus records and loading (SPEC 2.2, 2.4)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DietValue = Literal["yes", "no", "unknown"]


class Recipe(BaseModel):
    """One Wikibooks Cookbook page as a retrieval unit (SPEC 2.2)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    title: str
    url: str
    page_id: int
    revision_id: int
    categories: list[str]
    has_summary_template: bool
    total_minutes: int | None
    diet: dict[str, DietValue]
    ingredients: list[str]
    method: str
    summary: str


class Corpus(BaseModel):
    """The committed `data/corpus.json`."""

    model_config = ConfigDict(extra="forbid")

    corpus_version: str
    source: str
    license: str
    built_at: str
    recipes: list[Recipe]

    def by_id(self) -> dict[str, Recipe]:
        return {r.id: r for r in self.recipes}


def canonical_json(value: Any) -> str:
    """Sorted keys, no insignificant whitespace, UTF-8 characters kept."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_version(recipes: list[dict[str, Any]]) -> str:
    """SPEC 2.4: first 12 hex characters of SHA-256 over the canonical JSON of the recipes."""
    digest = hashlib.sha256(canonical_json(recipes).encode("utf-8")).hexdigest()
    return digest[:12]


def load_corpus(path: Path) -> Corpus:
    """Load and verify the committed corpus; raises ValueError if the stored version is stale."""
    corpus = Corpus.model_validate_json(path.read_text(encoding="utf-8"))
    expected = compute_version([r.model_dump() for r in corpus.recipes])
    if corpus.corpus_version != expected:
        msg = f"corpus_version {corpus.corpus_version} does not match content hash {expected}"
        raise ValueError(msg)
    return corpus
