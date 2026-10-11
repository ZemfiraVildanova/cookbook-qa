"""Behavior constants and environment settings.

Every name in this module has a row in SPEC.md section 8. Constants without an `Env`
entry in that table are module-level and cannot be overridden by the environment.
Values with an `Env` entry live in `Settings` and are read from the environment or a
local `.env` file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final, Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Validation (SPEC 3.2) -------------------------------------------------------------
QUESTION_MIN_CHARS: Final = 1
QUESTION_MAX_CHARS: Final = 500

# --- Retrieval (SPEC 4, ADR-001) --------------------------------------------------------
TOP_K: Final = 4
RRF_K: Final = 60
BM25_TITLE_BOOST: Final = 3
BM25_K1: Final = 1.5
BM25_B: Final = 0.75
BM25_TOKEN_PATTERN: Final = re.compile(r"[a-z0-9]+")
EMBEDDING_MODEL: Final = "text-embedding-3-small"
EMBEDDING_DIM: Final = 1536
EMBED_DOC_MAX_CHARS: Final = 8000
EMBED_TIMEOUT_S: Final = 5.0
SIMILARITY_FLOOR: Final[float | None] = None  # disabled unless a tuning set shows a margin (R-21)

# --- Constraint parser (SPEC 5.2) -------------------------------------------------------
QUICK_MAX_MINUTES: Final = 30
QUICK_WORDS: Final = ("quick", "fast", "speedy", "in a hurry", "in no time")
_TIME_UNIT: Final = r"(?P<unit>minutes?|mins?|min|hours?|hrs?|hr)"
TIME_PATTERNS: Final = (
    # "under 30 minutes", "in less than an hour and a half" is not supported; ranges "30-40 min"
    re.compile(
        r"\b(?:under|within|in|less than|no more than|at most|max(?:imum)?|up to)\s+"
        r"(?:about\s+|around\s+|roughly\s+)?"
        r"(?P<n>\d+(?:\.\d+)?)(?:\s*(?:-|\u2013|to)\s*(?P<n2>\d+(?:\.\d+)?))?\s*"
        + _TIME_UNIT
        + r"\b"
    ),
    # "a 20-minute meal", "15 minute dinner"
    re.compile(r"\b(?P<n>\d+)[- ](?P<unit>minute|min|hour)\b"),
    # "in an hour", "within one hour", "in half an hour"
    re.compile(r"\b(?:in|within|under)\s+(?P<phrase>half an hour|an hour|one hour)\b"),
)
TIME_PHRASE_MINUTES: Final = {"half an hour": 30, "an hour": 60, "one hour": 60}
DIET_VOCABULARY: Final[dict[str, tuple[str, ...]]] = {
    "vegetarian": ("vegetarian", "veggie", "meat-free", "meatless"),
    "vegan": ("vegan", "plant-based"),
}
INCLUDE_MARKERS: Final = (
    "made with",
    "that has",
    "that have",
    "that uses",
    "that contains",
    "that contain",
    "containing",
    "using",
    "with",
)
EXCLUDE_MARKERS: Final = ("without", "free of", "no")
EXCLUDE_SUFFIX: Final = "-free"  # "sugar-free" excludes sugar; an allergen word is safety (R-33)
INGREDIENT_STOPWORDS: Final = frozenset(
    {
        "a",
        "an",
        "the",
        "some",
        "any",
        "my",
        "fresh",
        "lots",
        "of",
        "one",
        "way",
        "time",
        "clue",
        "idea",
    }
)
INGREDIENT_MIN_CHARS: Final = 3
INGREDIENT_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "nut": (
        "nut",
        "almond",
        "walnut",
        "pecan",
        "cashew",
        "hazelnut",
        "pistachio",
        "peanut",
        "macadamia",
    ),
    "dairy": ("milk", "butter", "cheese", "cream", "yogurt", "yoghurt", "ghee"),
    "meat": (
        "meat",
        "beef",
        "pork",
        "lamb",
        "veal",
        "chicken",
        "turkey",
        "duck",
        "bacon",
        "ham",
        "sausage",
        "prosciutto",
        "guanciale",
        "pancetta",
        "lard",
        "suet",
        "chicken stock",
        "beef stock",
    ),
    "fish": ("fish", "anchovy", "anchovies", "salmon", "tuna", "cod", "fish sauce"),
    "seafood": (
        "fish",
        "anchovy",
        "anchovies",
        "salmon",
        "tuna",
        "cod",
        "fish sauce",
        "shrimp",
        "prawn",
        "crab",
        "lobster",
        "clam",
        "mussel",
        "oyster",
        "squid",
        "oyster sauce",
    ),
}

# --- Safety gate (SPEC 5.4) -------------------------------------------------------------
ALLERGEN_LEXICON: Final = (
    "nut",
    "nuts",
    "peanut",
    "tree nut",
    "almond",
    "walnut",
    "cashew",
    "pecan",
    "hazelnut",
    "pistachio",
    "gluten",
    "wheat",
    "dairy",
    "milk",
    "lactose",
    "egg",
    "eggs",
    "soy",
    "soya",
    "shellfish",
    "fish",
    "sesame",
    "mustard",
    "celery",
    "sulphite",
    "sulfite",
    "lupin",
    "mollusc",
)
_ALLERGENS: Final = "|".join(re.escape(a) for a in ALLERGEN_LEXICON)
SAFETY_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "allergy": re.compile(r"\ballerg\w*", re.IGNORECASE),
    "intolerance": re.compile(r"\bintoleran\w*", re.IGNORECASE),
    "allergen_free": re.compile(
        r"\b(?:" + _ALLERGENS + r")[- ]free\b|\bfree of (?:" + _ALLERGENS + r")\b", re.IGNORECASE
    ),
    "safe": re.compile(r"\bsafe (?:to eat|for)\b|\bis (?:it|this|that) safe\b", re.IGNORECASE),
    "pregnancy": re.compile(r"\bpregnan\w*|\bbreastfeed\w*|\bnursing mother", re.IGNORECASE),
    "infant": re.compile(r"\binfants?\b|\btoddlers?\b|\bfor (?:my|a|the) baby\b", re.IGNORECASE),
    "celiac": re.compile(r"\bco?eliac\b", re.IGNORECASE),
    "anaphylaxis": re.compile(r"\banaphyla\w*", re.IGNORECASE),
    "diabetes": re.compile(r"\bdiabet\w*", re.IGNORECASE),
    "poisoning": re.compile(r"\bfood poisoning\b|\bsalmonella\b|\blisteria\b", re.IGNORECASE),
    "raw_egg": re.compile(r"\braw eggs?\b.*\b(?:safe|risk|ok|okay)\b", re.IGNORECASE),
}

# --- Diet metadata at ingest (SPEC 2.3) -------------------------------------------------
VEGETARIAN_VETO_TERMS: Final = (
    "meat",
    "beef",
    "pork",
    "lamb",
    "veal",
    "chicken",
    "turkey",
    "duck",
    "bacon",
    "ham",
    "sausage",
    "prosciutto",
    "guanciale",
    "pancetta",
    "anchovy",
    "anchovies",
    "fish",
    "salmon",
    "tuna",
    "cod",
    "shrimp",
    "prawn",
    "crab",
    "lobster",
    "clam",
    "mussel",
    "oyster",
    "squid",
    "gelatin",
    "gelatine",
    "lard",
    "suet",
    "chicken stock",
    "beef stock",
    "fish sauce",
    "oyster sauce",
    "worcestershire",
)
VEGAN_VETO_TERMS: Final = (
    *VEGETARIAN_VETO_TERMS,
    "milk",
    "butter",
    "cheese",
    "cream",
    "yogurt",
    "yoghurt",
    "egg",
    "eggs",
    "honey",
    "ghee",
    "whey",
    "parmesan",
    "pecorino",
    "mozzarella",
    "ricotta",
    "mascarpone",
)
VETO_EXCEPTIONS: Final = (
    "coconut milk",
    "almond milk",
    "soy milk",
    "oat milk",
    "rice milk",
    "coconut cream",
    "cream of tartar",
    "peanut butter",
    "almond butter",
    "cocoa butter",
    "nut butter",
    "vegetable stock",
    "vegetable broth",
)
DIET_CATEGORIES: Final = {"vegetarian": "Vegetarian recipes", "vegan": "Vegan recipes"}

# --- Page structure at ingest (SPEC 2.2) -----------------------------------------------
INGREDIENT_HEADINGS: Final = ("ingredient",)  # substring match on section titles
METHOD_HEADINGS: Final = (
    "procedure",
    "preparation",
    "method",
    "directions",
    "instructions",
    "steps",
)
TIME_TOTAL_LABELS: Final = (
    "total",
    "time",
    "total time",
    "ready in",
)  # single labelled part = total

# --- Generation (SPEC 5.6) --------------------------------------------------------------
LLM_TEMPERATURE: Final = 0.0
LLM_MAX_OUTPUT_TOKENS: Final = 600
LLM_TIMEOUT_S: Final = 15.0
LLM_MAX_RETRIES: Final = 1
PROMPT_VERSION: Final = "answer.v1"
PROMPT_RECIPE_MAX_CHARS: Final = 3000

# --- Serving (SPEC 3.1) -----------------------------------------------------------------
STATIC_DIR: Final = Path("static")  # built UI; a placeholder page is served when absent

# --- Refusal texts (SPEC 6) -------------------------------------------------------------
REFUSAL_TEMPLATES: Final = {
    "out_of_corpus": (
        "Sorry, I couldn't find a recipe in this cookbook that answers your question. "
        "The corpus is a small selection of Wikibooks Cookbook recipes; try another dish "
        "or relax the constraints."
    ),
    "out_of_domain": "Sorry, I can only answer questions about the recipes in this cookbook.",
}
SAFETY_DISCLAIMER: Final = (
    "I can't give allergy, dietary-medical or food-safety advice, and I can't confirm whether "
    "a dish is free of any ingredient. Below are the ingredient lists exactly as the closest "
    "recipes in this cookbook state them. Please check them yourself and ask a qualified "
    "professional if you have a medical concern."
)


class Settings(BaseSettings):
    """Environment-backed settings (the `Env` column of SPEC section 8)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    llm_model: str = "claude-haiku-5-5"
    corpus_path: Path = Path("data/corpus.json")
    embeddings_path: Path = Path("data/embeddings.npz")
    git_sha: str = "unknown"
    log_level: str = "INFO"
    port: int = 8000
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None


def get_settings() -> Settings:
    """Build settings from the environment; called once at application start."""
    return Settings()
