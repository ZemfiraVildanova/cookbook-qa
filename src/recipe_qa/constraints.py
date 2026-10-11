"""Constraint parsing and three-valued filters (SPEC 5.2, rules R-10 to R-19).

Parsing order (R-10): time phrases first (removed from the text), then diet words, then
ingredient markers. A candidate ingredient becomes a constraint only if it matches at least
one ingredient line of the corpus by the rule of the filter that will apply it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from recipe_qa.config import (
    ALLERGEN_LEXICON,
    DIET_VOCABULARY,
    EXCLUDE_MARKERS,
    EXCLUDE_SUFFIX,
    INCLUDE_MARKERS,
    INGREDIENT_GROUPS,
    INGREDIENT_MIN_CHARS,
    INGREDIENT_STOPWORDS,
    QUICK_MAX_MINUTES,
    QUICK_WORDS,
    TIME_PATTERNS,
    TIME_PHRASE_MINUTES,
)
from recipe_qa.corpus import Recipe
from recipe_qa.ingest import strip_veto_exceptions
from recipe_qa.schemas import AppliedConstraints, Diet

_WORD_RE = re.compile(r"[a-z][a-z'-]*")
_MARKER_RE = re.compile(
    r"\b("
    + "|".join(
        re.escape(m) for m in sorted(INCLUDE_MARKERS + EXCLUDE_MARKERS, key=len, reverse=True)
    )
    + r")\b"
)
_SAFETY_EXCLUDE_MARKERS = frozenset({"free of"})  # with an allergen this is a safety question


# --- term normalisation and matching -------------------------------------------------------


def singularise(word: str) -> str:
    """Fixed plural rule: -ies → -y; -oes/-ches/-shes/-xes/-sses drop -es; else drop -s."""
    w = word.lower()
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if re.search(r"(oes|ches|shes|xes|sses)$", w):
        return w[:-2]
    if w.endswith("s") and not re.search(r"(ss|us|is|ous)$", w):
        return w[:-1]
    return w


def expand_terms(term: str) -> tuple[str, ...]:
    """INGREDIENT_GROUPS: a group word expands to its members, any other word stays."""
    return INGREDIENT_GROUPS.get(term, (term,))


def _include_match(terms: tuple[str, ...], line_lower: str) -> bool:
    """R-14: whole word with plural tolerance."""
    return any(re.search(rf"\b{re.escape(t)}(?:es|s)?\b", line_lower) for t in terms)


def _exclude_match(terms: tuple[str, ...], line_lower: str, dairy: bool) -> bool:
    """R-15: substring; VETO_EXCEPTIONS are removed first for the dairy group only."""
    text = strip_veto_exceptions(line_lower) if dairy else line_lower
    return any(t in text for t in terms)


class IngredientIndex:
    """Lower-cased ingredient lines of the whole corpus, used to accept parser candidates."""

    def __init__(self, lines: Iterable[str]) -> None:
        self.lines = [line.lower() for line in lines]

    def accepts_include(self, term: str) -> bool:
        terms = expand_terms(term)
        return any(_include_match(terms, line) for line in self.lines)

    def accepts_exclude(self, term: str) -> bool:
        terms = expand_terms(term)
        dairy = term == "dairy"
        return any(_exclude_match(terms, line, dairy) for line in self.lines)


# --- parser (R-10, R-13, R-17, R-33) ----------------------------------------------------------


def _parse_time(text: str) -> tuple[int | None, str]:
    """R-17: explicit numbers and phrases first, then quick words; the match is cut out."""
    for pattern in TIME_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        groups = m.groupdict()
        phrase = groups.get("phrase")
        if phrase:
            minutes = TIME_PHRASE_MINUTES[phrase]
        else:
            value = float(groups["n"])
            if groups.get("n2"):
                value = max(value, float(groups["n2"]))
            minutes = round(value * 60) if groups["unit"].startswith("h") else round(value)
        return minutes, text[: m.start()] + " " + text[m.end() :]
    for word in QUICK_WORDS:
        m = re.search(rf"\b{re.escape(word)}\b", text)
        if m:
            return QUICK_MAX_MINUTES, text[: m.start()] + " " + text[m.end() :]
    return None, text


def _parse_diet(text: str) -> tuple[Diet | None, str]:
    """R-13: vegan wins over vegetarian; matched words are cut out."""
    found: Diet | None = None
    for diet_name in ("vegan", "vegetarian"):
        for word in DIET_VOCABULARY[diet_name]:
            pattern = rf"\b{re.escape(word)}\b"
            if re.search(pattern, text):
                found = found or Diet(diet_name)
                text = re.sub(pattern, " ", text)
    return found, text


def _candidate_after(text: str) -> str | None:
    """First word after a marker that is not a stopword; None if too short (R-10)."""
    for word in _WORD_RE.findall(text):
        if word in INGREDIENT_STOPWORDS:
            continue
        return word if len(word) >= INGREDIENT_MIN_CHARS else None
    return None


def _is_allergen(term: str) -> bool:
    return term in ALLERGEN_LEXICON or singularise(term) in ALLERGEN_LEXICON


def _add(bucket: list[str], term: str) -> None:
    if term not in bucket:
        bucket.append(term)


def parse_constraints(question: str, index: IngredientIndex) -> AppliedConstraints:
    """Deterministic, English-only parser; returns exactly what the filters will use."""
    text = question.lower()
    max_minutes, text = _parse_time(text)
    diet, text = _parse_diet(text)

    include: list[str] = []
    exclude: list[str] = []

    # "X-free": an allergen is a safety question (R-33); anything else excludes X.
    for token in _WORD_RE.findall(text):
        if token.endswith(EXCLUDE_SUFFIX) and len(token) > len(EXCLUDE_SUFFIX):
            raw = token[: -len(EXCLUDE_SUFFIX)]
            term = singularise(raw)
            if _is_allergen(raw) or len(raw) < INGREDIENT_MIN_CHARS:
                continue
            if index.accepts_exclude(term):
                _add(exclude, term)

    for m in _MARKER_RE.finditer(text):
        marker = m.group(1)
        candidate = _candidate_after(text[m.end() :])
        if candidate is None or candidate.endswith(EXCLUDE_SUFFIX):
            continue
        term = singularise(candidate)
        if marker in EXCLUDE_MARKERS:
            if marker in _SAFETY_EXCLUDE_MARKERS and _is_allergen(candidate):
                continue
            if index.accepts_exclude(term):
                _add(exclude, term)
        elif index.accepts_include(term):
            _add(include, term)

    return AppliedConstraints(
        max_minutes=max_minutes,
        diet=diet,
        include_ingredients=include,
        exclude_ingredients=exclude,
    )


# --- filters (R-11 to R-15) ------------------------------------------------------------------


def recipe_passes(recipe: Recipe, constraints: AppliedConstraints) -> bool:
    raise NotImplementedError


def apply_filters(recipes: Sequence[Recipe], constraints: AppliedConstraints) -> list[Recipe]:
    raise NotImplementedError
