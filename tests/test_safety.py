"""Safety gate tests (SPEC 5.4, R-30..R-35). Deterministic, no model."""

from __future__ import annotations

import pytest

from recipe_qa import safety
from recipe_qa.config import SAFETY_DISCLAIMER
from tests.fakes import make_recipe


@pytest.mark.parametrize(
    ("question", "pattern"),
    [
        ("Is this nut-free?", "allergen_free"),
        ("Is the carbonara gluten free?", "allergen_free"),
        ("Which desserts are free of dairy?", "allergen_free"),
        ("egg-free cake recipe", "allergen_free"),
        ("I'm allergic to peanuts, which dessert can I eat?", "allergy"),
        ("Any allergens in the pesto?", "allergy"),
        ("a dessert for someone with lactose intolerance", "intolerance"),
        ("Is it safe to eat raw eggs while pregnant?", "safe"),
        ("Is this safe for my toddler?", "safe"),
        ("Can I eat this while pregnant?", "pregnancy"),
        ("Can diabetics eat this cake?", "diabetes"),
        ("coeliac friendly bread", "celiac"),
        ("Is there any risk of food poisoning with raw egg mousse?", "poisoning"),
        ("Is it OK to use raw eggs in this? Any risk?", "raw_egg"),
        ("could this cause anaphylaxis?", "anaphylaxis"),
        ("what can I feed an infant from this cookbook?", "infant"),
    ],
)
def test_safety_patterns_match(question: str, pattern: str) -> None:
    """R-30: declared patterns, each named so the log can say which one fired."""
    assert safety.check_safety(question) == pattern


@pytest.mark.parametrize(
    "question",
    [
        "baby spinach salad",
        "coconut soup",
        "nutmeg cookies",
        "donut recipe",
        "sugar-free dessert",
        "dessert without nuts",
        "a safe bet for a weeknight dinner",
        "How do I make carbonara?",
        "What's a vegetarian dinner I can make in under 30 minutes?",
        "pasta with eggs",
        "fish soup recipe",
    ],
)
def test_safety_patterns_do_not_match(question: str) -> None:
    """E-09, E-11, E-19: ordinary questions and exclusions are not safety questions."""
    assert safety.check_safety(question) is None


def test_safety_answer_lists_ingredients() -> None:
    """R-31: disclaimer, then each retrieved recipe's title and verbatim ingredient lines."""
    recipes = [
        make_recipe("walnut-cake", "Walnut Cake", ingredients=["200 g walnuts", "4 eggs"]),
        make_recipe("tomato-soup", "Tomato Soup", ingredients=["6 tomatoes", "1 onion"]),
    ]
    text = safety.safety_answer(recipes)
    assert text.startswith(SAFETY_DISCLAIMER)
    for line in ("200 g walnuts", "4 eggs", "6 tomatoes", "1 onion"):
        assert line in text
    assert text.index("Walnut Cake") < text.index("Tomato Soup")
    assert "free of" not in text.lower().replace(SAFETY_DISCLAIMER.lower(), "")


def test_safety_answer_without_recipes_is_disclaimer_only() -> None:
    """R-35: nothing retrieved means the disclaimer alone."""
    assert safety.safety_answer([]) == SAFETY_DISCLAIMER


def test_safety_answer_never_asserts_absence() -> None:
    """R-32: the text never says a dish is free of something or safe for a condition."""
    text = safety.safety_answer([make_recipe("x", "X", ingredients=["salt"])]).lower()
    body = text.replace(SAFETY_DISCLAIMER.lower(), "")
    for phrase in ("is nut-free", "is safe", "does not contain", "contains no", "free of"):
        assert phrase not in body


@pytest.mark.xfail(strict=True, reason="implemented in next commit")
def test_safety_scope_keeps_titled_recipes() -> None:
    """R-36, E-33: only the retrieved recipes whose title shares a word with the question."""
    carbonara = make_recipe("spaghetti-alla-carbonara", "Spaghetti alla Carbonara")
    pesto1 = make_recipe("pesto-i", "Pesto I")
    pesto2 = make_recipe("pesto-ii", "Pesto II")
    mac = make_recipe("macaroni-and-cheese-vegan", "Macaroni and Cheese (Vegan)")
    retrieved = [pesto1, carbonara, pesto2, mac]
    assert safety.safety_scope("Is the carbonara nut-free?", retrieved) == [carbonara]
    # plural tolerance (R-14 singularisation) and retrieval order kept
    assert safety.safety_scope("Are the pestos safe for toddlers?", retrieved) == [pesto1, pesto2]
    # case-insensitive, words inside parentheses count
    assert safety.safety_scope("Is the VEGAN one dairy-free?", retrieved) == [mac]
    # a title stopword shared with the question ("the", "and", "I") does not count
    assert safety.safety_scope("Is this nut-free? I need to know", retrieved) == retrieved


@pytest.mark.xfail(strict=True, reason="implemented in next commit")
def test_safety_scope_falls_back_to_all() -> None:
    """R-36, E-34: no shared title word means every retrieved recipe, in order; [] stays []."""
    recipes = [make_recipe("pesto-i", "Pesto I"), make_recipe("gazpacho", "Gazpacho")]
    assert safety.safety_scope("Is this nut-free?", recipes) == recipes
    assert safety.safety_scope("Is the gazpacho safe when pregnant?", recipes) == recipes[1:]
    assert safety.safety_scope("Is this nut-free?", []) == []


def test_title_stopwords_are_plain_tokens() -> None:
    """Section 8: TITLE_STOPWORDS are lower-case BM25 tokens, so they can match title tokens."""
    from recipe_qa.config import BM25_TOKEN_PATTERN, TITLE_STOPWORDS

    for word in TITLE_STOPWORDS:
        assert BM25_TOKEN_PATTERN.fullmatch(word), word
