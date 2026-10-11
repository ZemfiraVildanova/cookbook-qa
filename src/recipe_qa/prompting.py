"""Prompt assembly (SPEC 5.6: R-40, R-51, R-55, R-56).

The system prompt is a versioned file under `prompts/`; the user message carries the parsed
constraints, the retrieved recipes and the question as delimited data blocks.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from recipe_qa.config import PROMPT_RECIPE_MAX_CHARS, PROMPT_VERSION
from recipe_qa.corpus import Recipe
from recipe_qa.llm import GenerationRequest
from recipe_qa.schemas import AppliedConstraints

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
TRUNCATION_MARKER = "[truncated]"


@lru_cache(maxsize=4)
def load_system_prompt(version: str = PROMPT_VERSION) -> str:
    return (PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8").strip()


def render_recipe(recipe: Recipe) -> str:
    """One recipe as a data block; the method is cut at PROMPT_RECIPE_MAX_CHARS (R-55)."""
    method = recipe.method
    if len(method) > PROMPT_RECIPE_MAX_CHARS:
        method = method[:PROMPT_RECIPE_MAX_CHARS].rstrip() + " " + TRUNCATION_MARKER
    minutes = recipe.total_minutes if recipe.total_minutes is not None else "unknown"
    ingredients = "\n".join(f"- {line}" for line in recipe.ingredients) or "- (not listed)"
    return (
        f'<recipe id="{recipe.id}">\n'
        f"title: {recipe.title}\n"
        f"total_minutes: {minutes}\n"
        f"vegetarian: {recipe.diet['vegetarian']}\n"
        f"vegan: {recipe.diet['vegan']}\n"
        f"ingredients:\n{ingredients}\n"
        f"method:\n{method}\n"
        f"</recipe>"
    )


def build_request(
    question: str, constraints: AppliedConstraints, recipes: list[Recipe]
) -> GenerationRequest:
    """System prompt from the versioned file; user message with delimited data (R-40)."""
    blocks = "\n".join(render_recipe(r) for r in recipes)
    user = (
        "<constraints>\n"
        f"{json.dumps(constraints.model_dump(mode='json'), ensure_ascii=False)}\n"
        "</constraints>\n"
        "<recipes>\n"
        f"{blocks}\n"
        "</recipes>\n"
        "<question>\n"
        f"{question}\n"
        "</question>\n"
        "Return only the JSON object."
    )
    return GenerationRequest(
        system=load_system_prompt(),
        user=user,
        allowed_ids=[r.id for r in recipes],
    )
