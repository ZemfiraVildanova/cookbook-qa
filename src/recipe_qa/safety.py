"""Safety gate and safety answer (SPEC 5.4, R-30..R-36).

"Careful" means: never a verdict. A matching question is refused before the model runs; the
answer is a fixed disclaimer followed by the shown recipes' ingredient lines verbatim. The
shown recipes are those retrieved recipes whose title the question names (R-36), or all of
them when it names none.
"""

from __future__ import annotations

from collections.abc import Sequence

from recipe_qa.config import (
    BM25_TOKEN_PATTERN,
    SAFETY_DISCLAIMER,
    SAFETY_PATTERNS,
    TITLE_STOPWORDS,
)
from recipe_qa.constraints import singularise
from recipe_qa.corpus import Recipe


def check_safety(question: str) -> str | None:
    """Name of the first SAFETY_PATTERNS entry that matches, or None (R-30)."""
    for name, pattern in SAFETY_PATTERNS.items():
        if pattern.search(question):
            return name
    return None


def _title_words(title: str) -> set[str]:
    """Significant title words: BM25 tokens minus TITLE_STOPWORDS, singularised (R-36)."""
    tokens = BM25_TOKEN_PATTERN.findall(title.lower())
    return {singularise(t) for t in tokens if t not in TITLE_STOPWORDS}


def safety_scope(question: str, recipes: Sequence[Recipe]) -> list[Recipe]:
    """Retrieved recipes whose title shares a significant word with the question (R-36).

    Falls back to every retrieved recipe when no title matches; order is kept.
    """
    asked = {singularise(t) for t in BM25_TOKEN_PATTERN.findall(question.lower())}
    shown = [r for r in recipes if _title_words(r.title) & asked]
    return shown or list(recipes)


def safety_answer(recipes: Sequence[Recipe]) -> str:
    """SAFETY_DISCLAIMER, then each shown recipe's title and verbatim ingredient lines (R-31)."""
    if not recipes:
        return SAFETY_DISCLAIMER
    blocks = []
    for recipe in recipes:
        lines = "\n".join(f"- {line}" for line in recipe.ingredients)
        if not lines:
            lines = "- (the page lists no ingredients)"
        blocks.append(f"{recipe.title}:\n{lines}")
    return SAFETY_DISCLAIMER + "\n\n" + "\n\n".join(blocks)
