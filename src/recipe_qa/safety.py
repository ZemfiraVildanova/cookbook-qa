"""Safety gate and safety answer (SPEC 5.4, R-30..R-35). Implemented in the next commit."""

from __future__ import annotations

from collections.abc import Sequence

from recipe_qa.corpus import Recipe


def check_safety(question: str) -> str | None:
    """Name of the first SAFETY_PATTERNS entry that matches, or None."""
    raise NotImplementedError


def safety_answer(recipes: Sequence[Recipe]) -> str:
    """SAFETY_DISCLAIMER followed by each recipe's title and verbatim ingredient lines."""
    raise NotImplementedError
