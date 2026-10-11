"""Constraint parsing and three-valued filters (SPEC 5.2). Implemented in the next commit."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from recipe_qa.corpus import Recipe
from recipe_qa.schemas import AppliedConstraints


class IngredientIndex:
    """Lower-cased ingredient lines of the whole corpus, used to accept parser candidates."""

    def __init__(self, lines: Iterable[str]) -> None:
        raise NotImplementedError


def singularise(word: str) -> str:
    raise NotImplementedError


def expand_terms(term: str) -> tuple[str, ...]:
    raise NotImplementedError


def parse_constraints(question: str, index: IngredientIndex) -> AppliedConstraints:
    raise NotImplementedError


def recipe_passes(recipe: Recipe, constraints: AppliedConstraints) -> bool:
    raise NotImplementedError


def apply_filters(recipes: Sequence[Recipe], constraints: AppliedConstraints) -> list[Recipe]:
    raise NotImplementedError
