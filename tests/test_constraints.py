"""Constraint parser and filter tests (SPEC 5.2, rules R-10..R-19). No network, no model."""

from __future__ import annotations

import pytest

from recipe_qa import constraints
from recipe_qa.corpus import Recipe
from recipe_qa.schemas import AppliedConstraints, Diet

CORPUS_LINES = [
    "2 cups flour",
    "3 eggs",
    "1 cup milk",
    "200 g spaghetti",
    "1 chicken breast",
    "1 cup chopped walnuts",
    "2 tomatoes",
    "1 tbsp olive oil",
    "1 can coconut milk",
    "1 cup sugar",
    "3 garlic cloves",
    "1 cup lentils",
    "a pinch of nutmeg",
    "2 tbsp butter",
    "parmesan cheese",
]


@pytest.fixture(scope="module")
def index() -> constraints.IngredientIndex:
    return constraints.IngredientIndex(CORPUS_LINES)


def ac(**kwargs: object) -> AppliedConstraints:
    return AppliedConstraints(**kwargs)  # type: ignore[arg-type]


# --- parser (R-10, R-13, R-17, R-33) ------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "What's a vegetarian dinner I can make in under 30 minutes?",
            ac(max_minutes=30, diet=Diet.VEGETARIAN),
        ),
        ("quick pasta with tomatoes", ac(max_minutes=30, include_ingredients=["tomato"])),
        ("quick dinner in 45 minutes", ac(max_minutes=45)),
        ("something I can make in an hour", ac(max_minutes=60)),
        ("ready in half an hour", ac(max_minutes=30)),
        ("a 20-minute meal", ac(max_minutes=20)),
        ("something under 2 hours", ac(max_minutes=120)),
        ("dinner in less than 1.5 hours", ac(max_minutes=90)),
        ("veggie pasta in 15\u201320 minutes", ac(max_minutes=20, diet=Diet.VEGETARIAN)),
        ("dessert without nuts", ac(exclude_ingredients=["nut"])),
        ("vegan dessert without nuts or dairy", ac(diet=Diet.VEGAN, exclude_ingredients=["nut"])),
        ("something in no more than 30 minutes", ac(max_minutes=30)),
        ("I have no idea what to cook", ac()),
        ("What can I cook with my kids?", ac()),
        ("dinner with friends", ac()),
        ("I have no time to cook", ac()),
        ("soup with no meat", ac(exclude_ingredients=["meat"])),
        ("sugar-free dessert", ac(exclude_ingredients=["sugar"])),
        ("nut-free dessert", ac()),  # allergen: the safety gate owns this (R-33)
        ("gluten-free bread", ac()),
        ("something made with chicken", ac(include_ingredients=["chicken"])),
        ("plant-based soup", ac(diet=Diet.VEGAN)),
        ("pasta that has eggs", ac(include_ingredients=["egg"])),
        ("cake using walnuts", ac(include_ingredients=["walnut"])),
        ("dinner without coconut milk", ac(exclude_ingredients=["coconut"])),
        ("meat-free lasagna", ac(diet=Diet.VEGETARIAN)),
        ("Is there anything with unicorn?", ac()),  # not an ingredient of the corpus
        ("vegan and vegetarian options", ac(diet=Diet.VEGAN)),
        ("no garlic please", ac(exclude_ingredients=["garlic"])),
        ("How do I make carbonara?", ac()),
    ],
)
def test_parse_constraints(
    question: str, expected: AppliedConstraints, index: constraints.IngredientIndex
) -> None:
    """R-10, R-13, R-17, R-33: table of phrasings."""
    assert constraints.parse_constraints(question, index) == expected


def test_non_ingredient_terms_ignored(index: constraints.IngredientIndex) -> None:
    """R-10: a candidate that matches no corpus ingredient line is not a constraint."""
    for q in ("dinner with friends", "cook with my kids", "no clue what to eat", "no way"):
        assert constraints.parse_constraints(q, index) == ac()


def test_time_phrase_parsed_before_markers(index: constraints.IngredientIndex) -> None:
    """R-10: 'no more than 30 minutes' is time, never an exclusion."""
    parsed = constraints.parse_constraints("pasta in no more than 30 minutes", index)
    assert parsed.max_minutes == 30
    assert parsed.exclude_ingredients == []


@pytest.mark.parametrize(
    ("word", "singular"),
    [
        ("tomatoes", "tomato"),
        ("eggs", "egg"),
        ("nuts", "nut"),
        ("cheese", "cheese"),
        ("olives", "olive"),
        ("berries", "berry"),
        ("hummus", "hummus"),
        ("peaches", "peach"),
    ],
)
def test_singularise(word: str, singular: str) -> None:
    assert constraints.singularise(word) == singular


def test_expand_terms() -> None:
    """INGREDIENT_GROUPS: a group word expands, an ordinary word stays."""
    assert "walnut" in constraints.expand_terms("nut") and "nut" in constraints.expand_terms("nut")
    assert constraints.expand_terms("tomato") == ("tomato",)


# --- filters (R-11..R-15) -----------------------------------------------------------------


def recipe(
    rid: str = "r",
    minutes: int | None = 20,
    vegetarian: str = "yes",
    vegan: str = "unknown",
    ingredients: list[str] | None = None,
) -> Recipe:
    return Recipe(
        id=rid,
        title=rid,
        url=f"https://en.wikibooks.org/wiki/Cookbook:{rid}",
        page_id=1,
        revision_id=1,
        categories=[],
        has_summary_template=True,
        total_minutes=minutes,
        diet={"vegetarian": vegetarian, "vegan": vegan},  # type: ignore[dict-item]
        ingredients=["1 cup flour", "2 eggs"] if ingredients is None else ingredients,
        method="Mix.",
        summary="",
    )


@pytest.mark.xfail(strict=True, reason="implemented in next commit")
def test_unknown_metadata_fails_filter() -> None:
    """R-11: unknown never satisfies a constraint."""
    assert not constraints.recipe_passes(recipe(minutes=None), ac(max_minutes=30))
    assert not constraints.recipe_passes(recipe(vegetarian="unknown"), ac(diet=Diet.VEGETARIAN))
    assert not constraints.recipe_passes(recipe(ingredients=[]), ac(include_ingredients=["egg"]))
    assert not constraints.recipe_passes(recipe(ingredients=[]), ac(exclude_ingredients=["egg"]))


@pytest.mark.xfail(strict=True, reason="implemented in next commit")
def test_max_minutes_boundary() -> None:
    """R-12: pass iff known and <= max_minutes."""
    assert constraints.recipe_passes(recipe(minutes=30), ac(max_minutes=30))
    assert not constraints.recipe_passes(recipe(minutes=31), ac(max_minutes=30))


@pytest.mark.xfail(strict=True, reason="implemented in next commit")
def test_diet_filter() -> None:
    """R-13: vegan requires vegan yes; vegetarian yes alone is not enough."""
    assert constraints.recipe_passes(
        recipe(vegetarian="yes", vegan="unknown"), ac(diet=Diet.VEGETARIAN)
    )
    assert not constraints.recipe_passes(
        recipe(vegetarian="yes", vegan="unknown"), ac(diet=Diet.VEGAN)
    )
    assert constraints.recipe_passes(recipe(vegetarian="yes", vegan="yes"), ac(diet=Diet.VEGAN))
    assert not constraints.recipe_passes(recipe(vegetarian="no"), ac(diet=Diet.VEGETARIAN))


@pytest.mark.xfail(strict=True, reason="implemented in next commit")
def test_include_is_whole_word_with_plurals() -> None:
    """R-14: whole-word match with plural tolerance; groups expand."""
    assert constraints.recipe_passes(
        recipe(ingredients=["2 tomatoes"]), ac(include_ingredients=["tomato"])
    )
    assert not constraints.recipe_passes(
        recipe(ingredients=["a pinch of nutmeg"]), ac(include_ingredients=["nut"])
    )
    assert not constraints.recipe_passes(
        recipe(ingredients=["1 eggplant"]), ac(include_ingredients=["egg"])
    )
    assert constraints.recipe_passes(
        recipe(ingredients=["1 cup walnuts"]), ac(include_ingredients=["nut"])
    )
    assert not constraints.recipe_passes(
        recipe(ingredients=["2 eggs"]), ac(include_ingredients=["egg", "milk"])
    )


@pytest.mark.xfail(strict=True, reason="implemented in next commit")
def test_exclude_is_substring_and_conservative() -> None:
    """R-15: substring match; 'nut' excludes nutmeg, walnuts and almonds."""
    assert not constraints.recipe_passes(
        recipe(ingredients=["a pinch of nutmeg"]), ac(exclude_ingredients=["nut"])
    )
    assert not constraints.recipe_passes(
        recipe(ingredients=["1 cup walnuts"]), ac(exclude_ingredients=["nut"])
    )
    assert constraints.recipe_passes(
        recipe(ingredients=["2 cups flour", "1 egg"]), ac(exclude_ingredients=["nut"])
    )


@pytest.mark.xfail(strict=True, reason="implemented in next commit")
def test_group_expansion_excludes_members() -> None:
    """E-32: 'without nuts' excludes a recipe listing almonds, with no word 'nut' anywhere."""
    almond_cake = recipe(ingredients=["200 g ground almonds", "4 eggs", "150 g sugar"])
    assert not constraints.recipe_passes(almond_cake, ac(exclude_ingredients=["nut"]))
    assert constraints.recipe_passes(almond_cake, ac(exclude_ingredients=["tomato"]))


@pytest.mark.xfail(strict=True, reason="implemented in next commit")
def test_dairy_group_respects_veto_exceptions() -> None:
    """R-15: VETO_EXCEPTIONS apply to the dairy group only."""
    assert constraints.recipe_passes(
        recipe(ingredients=["1 can coconut milk"]), ac(exclude_ingredients=["dairy"])
    )
    assert not constraints.recipe_passes(
        recipe(ingredients=["1 cup milk"]), ac(exclude_ingredients=["dairy"])
    )
    # almond milk is still a nut
    assert not constraints.recipe_passes(
        recipe(ingredients=["1 cup almond milk"]), ac(exclude_ingredients=["nut"])
    )


@pytest.mark.xfail(strict=True, reason="implemented in next commit")
def test_apply_filters_keeps_order() -> None:
    """R-11: filters keep the retrieval order of the survivors."""
    fast = recipe("fast", minutes=10)
    slow = recipe("slow", minutes=90)
    unknown = recipe("unknown", minutes=None)
    assert constraints.apply_filters([slow, fast, unknown], ac(max_minutes=30)) == [fast]
    assert constraints.apply_filters([slow, fast, unknown], ac()) == [slow, fast, unknown]
