"""Wikitext parser and corpus metadata tests (SPEC 2.2, 2.3, 2.4). Fixtures are real pages."""

from __future__ import annotations

from pathlib import Path

import pytest

from recipe_qa import ingest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.wikitext").read_text(encoding="utf-8")


# --- durations (SPEC 2.3) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("1 hour", 60),
        ("15 minutes", 15),
        ("45 min", 45),
        ("2 hrs", 120),
        ("½ hour", 30),
        ("1½ hours", 90),
        ("1 hour 15 min", 75),
        ("2\u20133 hours", 180),  # range: upper bound
        ("30 to 40 min", 40),
        ("45\u201360 minutes", 60),
        ("About 2 hours", 120),
        ("5 minutes per skillet", 5),
        ("1 day", 1440),
        ("overnight", None),
        ("30", None),  # bare number: unit unknown
        ("", None),
    ],
)
def test_parse_duration(text: str, minutes: int | None) -> None:
    """SPEC 2.3: duration parsing, ranges take the upper bound, non-numeric is unknown."""
    assert ingest.parse_duration(text) == minutes


@pytest.mark.parametrize(
    ("value", "minutes"),
    [
        ("1 hour", 60),
        ("Prep: 10 minutes<br/>Cooking: 5 minutes per skillet", 15),
        ("Prep: 15\u201320 minutes<br>Cooking time: 1½ hours", 110),
        ("Soak: overnight<br/>Prep: 15\u201320 minutes<br/>Cooking time: 1½ hours", None),
        ("Prep: 10 min", None),  # a single partial time is not a total
        ("Total: 1 hour", 60),
        ("", None),
    ],
)
def test_parse_time_field(value: str, minutes: int | None) -> None:
    """SPEC 2.3 rules 1-3: template time, summed parts, or unknown."""
    assert ingest.parse_time_field(value) == minutes


# --- template and sections ---------------------------------------------------------------


def test_template_params_lower_cased_keys() -> None:
    """SPEC 2.2: recipesummary parameters, both spellings, keys normalised."""
    params = ingest.parse_summary_template(fixture("spaghetti_alla_carbonara"))
    assert params is not None
    assert params["time"] == "1 hour"
    assert params["servings"] == "6"
    assert params["category"] == "Pasta recipes"
    macaroni = ingest.parse_summary_template(fixture("macaroni_and_cheese_vegan"))
    assert macaroni is not None
    assert macaroni["category"] == "Pasta recipes"
    assert macaroni["difficulty"] == "2"
    assert "time" not in macaroni
    assert ingest.parse_summary_template(fixture("edikang_ikong")) is None


def test_ingredient_lines_are_plain_text() -> None:
    """SPEC 2.2: ingredient lines from the ingredients section, markup removed."""
    lines = ingest.ingredient_lines(fixture("spaghetti_alla_carbonara"))
    assert lines[0] == "450 g (1 pound) spaghetti"
    assert any(line.startswith("5 egg yolks") for line in lines)
    assert all("[[" not in line and "{{" not in line for line in lines)


def test_ingredient_section_fallback_heading() -> None:
    """SPEC 2.2: without an Ingredients heading, the first bullet list before the method is used."""
    lines = ingest.ingredient_lines(fixture("edikang_ikong"))
    assert "Water Leaf" in lines
    assert any("Goat Meat" in line for line in lines)


def test_method_text_keeps_steps_and_drops_markup() -> None:
    """SPEC 2.2: method is plain text of the procedure section."""
    method = ingest.method_text(fixture("tuscan_bean_soup"))
    assert method.startswith("Soak beans and spelt overnight")
    assert "[[" not in method and "{{" not in method and "<ref" not in method
    assert "Serve immediately." in method
    edikang = ingest.method_text(fixture("edikang_ikong"))
    assert edikang.startswith("Add a half spoon of salt")


def test_summary_text_drops_refs_and_templates() -> None:
    """SPEC 2.2: lead paragraph cleaned of templates, refs and link markup."""
    summary = ingest.summary_text(fixture("spaghetti_alla_carbonara"))
    assert summary.startswith("Spaghetti alla carbonara (literally")
    assert "<ref" not in summary and "{{" not in summary and "[[" not in summary
    assert "black pepper" in summary


def test_categories_from_wikitext() -> None:
    """SPEC 2.2: category links are read without the Category: prefix."""
    cats = ingest.categories_from_wikitext(fixture("tuscan_bean_soup"))
    assert "Soup recipes" in cats and "Italian recipes" in cats
    assert all(not c.startswith("Category:") for c in cats)


# --- ids ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Spaghetti alla Carbonara", "spaghetti-alla-carbonara"),
        ("Macaroni and Cheese (Vegan)", "macaroni-and-cheese-vegan"),
        (
            "Vietnamese Rice Noodle and Beef Soup (Bún bò Huế)",
            "vietnamese-rice-noodle-and-beef-soup-bun-bo-hue",
        ),
        ("Cookbook:Hummus I", "hummus-i"),
    ],
)
def test_slug(title: str, slug: str) -> None:
    """SPEC 2.2: id is an ASCII slug of the title without the Cookbook: prefix."""
    assert ingest.slug(title) == slug


# --- diet metadata (SPEC 2.3) ------------------------------------------------------------


def test_diet_category_yes() -> None:
    diet = ingest.diet_flags(["Vegan recipes"], ["1 cup lentils", "2 carrots"])
    assert diet == {"vegetarian": "yes", "vegan": "yes"}
    diet = ingest.diet_flags(["Vegetarian recipes"], ["2 eggs", "1 cup milk"])
    assert diet == {"vegetarian": "yes", "vegan": "no"}


def test_diet_unknown_without_category() -> None:
    assert ingest.diet_flags(["Soup recipes"], ["1 cup lentils"]) == {
        "vegetarian": "unknown",
        "vegan": "unknown",
    }


def test_diet_veto_overrides_category() -> None:
    """SPEC 2.3: a veto term makes the recipe non-vegetarian even if the category says otherwise."""
    diet = ingest.diet_flags(
        ["Vegetarian recipes"], ["Goat Meat (preferred)", "Roasted or dried Fish"]
    )
    assert diet == {"vegetarian": "no", "vegan": "no"}
    diet = ingest.diet_flags(["Vegan recipes"], ["5 cups homemade chicken stock"])
    assert diet == {"vegetarian": "no", "vegan": "no"}


def test_veto_whole_word() -> None:
    """SPEC 2.3: veto terms are whole words (eggplant, graham crackers, butternut squash pass)."""
    diet = ingest.diet_flags(
        ["Vegan recipes"], ["1 large eggplant", "8 graham crackers", "1 butternut squash"]
    )
    assert diet == {"vegetarian": "yes", "vegan": "yes"}
    # plural tolerance still works
    assert ingest.diet_flags(["Vegan recipes"], ["2 large eggs"])["vegan"] == "no"
    assert ingest.diet_flags(["Vegetarian recipes"], ["4 anchovies"])["vegetarian"] == "no"


def test_veto_exceptions() -> None:
    """SPEC 2.3: VETO_EXCEPTIONS phrases do not trigger a veto."""
    diet = ingest.diet_flags(
        ["Vegan recipes"],
        [
            "1 can coconut milk",
            "2 tbsp peanut butter",
            "1 tsp cream of tartar",
            "4 cups vegetable stock",
        ],
    )
    assert diet == {"vegetarian": "yes", "vegan": "yes"}
    # the exception removes only the phrase: real dairy on the same line still counts
    assert ingest.diet_flags(["Vegan recipes"], ["coconut milk and 1 cup milk"])["vegan"] == "no"


# --- whole page and corpus version ---------------------------------------------------------


def test_parse_page_carbonara() -> None:
    """SPEC 2.2 record from a real page."""
    recipe = ingest.parse_page(
        title="Cookbook:Spaghetti alla Carbonara",
        page_id=1,
        revision_id=4640105,
        url="https://en.wikibooks.org/wiki/Cookbook:Spaghetti_alla_Carbonara",
        categories=["Italian recipes", "Recipes using pasta and noodles"],
        wikitext=fixture("spaghetti_alla_carbonara"),
    )
    assert recipe.id == "spaghetti-alla-carbonara"
    assert recipe.title == "Spaghetti alla Carbonara"
    assert recipe.has_summary_template is True
    assert recipe.total_minutes == 60
    assert recipe.diet == {"vegetarian": "no", "vegan": "no"}  # guanciale, egg yolks
    assert len(recipe.ingredients) >= 5
    assert recipe.method and recipe.summary
    assert "Italian recipes" in recipe.categories


def test_parse_page_without_template() -> None:
    recipe = ingest.parse_page(
        title="Cookbook:Edikang Ikong",
        page_id=2,
        revision_id=4522969,
        url="https://en.wikibooks.org/wiki/Cookbook:Edikang_Ikong",
        categories=["Vegetarian recipes", "Nigerian recipes"],
        wikitext=fixture("edikang_ikong"),
    )
    assert recipe.has_summary_template is False
    assert recipe.total_minutes is None
    assert recipe.diet["vegetarian"] == "no"
    assert len(recipe.ingredients) >= 10


def test_corpus_version_is_deterministic() -> None:
    """SPEC 2.4: first 12 hex chars of SHA-256 over canonical JSON of the recipes."""
    a = [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]
    b = [{"title": "B", "id": "b"}, {"title": "A", "id": "a"}][::-1]  # same content, keys reordered
    assert ingest.corpus_version(a) == ingest.corpus_version(b)
    assert len(ingest.corpus_version(a)) == 12
    assert ingest.corpus_version(a) != ingest.corpus_version(a[:1])


def test_ingredient_table_rows() -> None:
    """SPEC 2.2: an ingredients section laid out as a wikitable yields one line per row."""
    lines = ingest.ingredient_lines(fixture("focaccia_i"))
    assert len(lines) >= 5
    assert any(line.lower().startswith("active dry yeast") for line in lines)
    assert all("{|" not in line and "|-" not in line and "!" not in line for line in lines)
