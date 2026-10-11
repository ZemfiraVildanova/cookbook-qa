"""Safety gate and safety answer (SPEC 5.4, R-30..R-35).

"Careful" means: never a verdict. A matching question is refused before the model runs; the
answer is a fixed disclaimer followed by the retrieved recipes' ingredient lines verbatim.
"""

from __future__ import annotations

from collections.abc import Sequence

from recipe_qa.config import SAFETY_DISCLAIMER, SAFETY_PATTERNS
from recipe_qa.corpus import Recipe


def check_safety(question: str) -> str | None:
    """Name of the first SAFETY_PATTERNS entry that matches, or None (R-30)."""
    for name, pattern in SAFETY_PATTERNS.items():
        if pattern.search(question):
            return name
    return None


def safety_answer(recipes: Sequence[Recipe]) -> str:
    """SAFETY_DISCLAIMER, then each recipe's title and verbatim ingredient lines (R-31, R-35)."""
    if not recipes:
        return SAFETY_DISCLAIMER
    blocks = []
    for recipe in recipes:
        lines = "\n".join(f"- {line}" for line in recipe.ingredients)
        if not lines:
            lines = "- (the page lists no ingredients)"
        blocks.append(f"{recipe.title}:\n{lines}")
    return SAFETY_DISCLAIMER + "\n\n" + "\n\n".join(blocks)
