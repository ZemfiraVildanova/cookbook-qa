"""Wikitext parsing and corpus metadata (SPEC 2.2, 2.3, 2.4).

Pure functions over page text; no network. `scripts/ingest.py` fetches pages through the
MediaWiki API and calls `parse_page`.
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any

from recipe_qa.config import (
    DIET_CATEGORIES,
    INGREDIENT_HEADINGS,
    METHOD_HEADINGS,
    TIME_TOTAL_LABELS,
    VEGAN_VETO_TERMS,
    VEGETARIAN_VETO_TERMS,
    VETO_EXCEPTIONS,
)
from recipe_qa.corpus import Recipe, compute_version

# --- durations (SPEC 2.3) ------------------------------------------------------------------

_VULGAR_FRACTIONS = {
    "½": 0.5,
    "¼": 0.25,
    "¾": 0.75,
    "⅓": 1 / 3,
    "⅔": 2 / 3,
    "⅛": 0.125,
}
_FRACTION_RE = re.compile(r"(\d+)?\s*([½¼¾⅓⅔⅛])")
_DURATION_RE = re.compile(
    r"(?P<n>\d+(?:\.\d+)?)(?:\s*(?:-|\u2013|\u2014|to)\s*(?P<n2>\d+(?:\.\d+)?))?\s*"
    r"(?P<unit>minutes?|mins?|min|hours?|hrs?|hr|h|days?)\b",
    re.IGNORECASE,
)
_TIME_PART_SPLIT = re.compile(r"<br\s*/?>|\n|;", re.IGNORECASE)
_TIME_LABEL_RE = re.compile(r"^(?P<label>[A-Za-z][A-Za-z ]{0,30}?)\s*:\s*(?P<value>.+)$")


def _normalise_fractions(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        whole = float(m.group(1)) if m.group(1) else 0.0
        return f"{whole + _VULGAR_FRACTIONS[m.group(2)]:g}"

    return _FRACTION_RE.sub(repl, text)


def parse_duration(text: str) -> int | None:
    """Minutes in a free-text duration; ranges take the upper bound; no unit means unknown."""
    normalised = _normalise_fractions(text.lower())
    total = 0.0
    found = False
    for m in _DURATION_RE.finditer(normalised):
        value = float(m.group("n2") or m.group("n"))
        unit = m.group("unit")
        if unit.startswith("m"):
            factor = 1
        elif unit.startswith("h"):
            factor = 60
        else:
            factor = 1440
        total += value * factor
        found = True
    return round(total) if found else None


def parse_time_field(value: str) -> int | None:
    """Total minutes from a `time=` value (SPEC 2.3 rules 1 to 3).

    One unlabelled value, or one labelled with a total label, is parsed as the total.
    Two or more labelled parts (prep, cook, soak, ...) are summed when every part parses;
    a single partial part, or any unparseable part, gives unknown.
    """
    parts = [p.strip() for p in _TIME_PART_SPLIT.split(value) if p.strip()]
    if not parts:
        return None
    labelled: list[tuple[str | None, str]] = []
    for part in parts:
        m = _TIME_LABEL_RE.match(part)
        if m:
            labelled.append((m.group("label").strip().lower(), m.group("value")))
        else:
            labelled.append((None, part))
    if len(labelled) == 1:
        label, text = labelled[0]
        if label is None or label in TIME_TOTAL_LABELS:
            return parse_duration(text)
        return None
    minutes = [parse_duration(text) for _, text in labelled]
    if any(m is None for m in minutes):
        return None
    return sum(m for m in minutes if m is not None)


# --- markup -------------------------------------------------------------------------------

_TEMPLATE_RE = re.compile(r"\{\{\s*recipe\s*_?\s*summary\s*(?P<params>\|.*?)?\}\}", re.I | re.S)
_REF_RE = re.compile(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_ANY_TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}", re.S)
_MEDIA_LINK_RE = re.compile(r"\[\[\s*(?:File|Image|Category)\s*:[^\[\]]*\]\]", re.I)
_LINK_RE = re.compile(r"\[\[([^\[\]|]*)(?:\|([^\[\]]*))?\]\]")
_EXT_LINK_RE = re.compile(r"\[(?:https?|ftp)://[^\s\]]+(?:\s+([^\]]*))?\]")
_HEADING_RE = re.compile(r"^(?P<eq>={2,5})\s*(?P<title>.+?)\s*(?P=eq)\s*$", re.M)
_CATEGORY_RE = re.compile(r"\[\[\s*Category\s*:\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]\]", re.I)
_MAGIC_WORD_RE = re.compile(r"__[A-Z]+__")
_TAG_RE = re.compile(r"<[^>]+>")


def clean_wikitext(text: str) -> str:
    """Plain text: refs, comments, templates and media links removed; links keep display text."""
    text = _REF_RE.sub("", text)
    text = _COMMENT_RE.sub("", text)
    while True:
        new = _ANY_TEMPLATE_RE.sub("", text)
        if new == text:
            break
        text = new
    while True:
        new = _MEDIA_LINK_RE.sub("", text)
        if new == text:
            break
        text = new

    def link(m: re.Match[str]) -> str:
        target, display = m.group(1), m.group(2)
        shown = display if display is not None and display.strip() else target
        return re.sub(r"^\s*Cookbook\s*:", "", shown).strip()

    text = _LINK_RE.sub(link, text)
    text = _EXT_LINK_RE.sub(lambda m: m.group(1) or "", text)
    text = text.replace("'''''", "").replace("'''", "").replace("''", "")
    text = _MAGIC_WORD_RE.sub("", text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(lines)


def parse_summary_template(wikitext: str) -> dict[str, str] | None:
    """Parameters of `{{recipesummary ...}}` with lower-cased keys, or None when absent."""
    m = _TEMPLATE_RE.search(wikitext)
    if not m:
        return None
    params: dict[str, str] = {}
    for part in (m.group("params") or "").split("|"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            params[key] = value
    return params


def _sections(wikitext: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Lead text and a list of (level, title, body) in document order."""
    matches = list(_HEADING_RE.finditer(wikitext))
    lead = wikitext[: matches[0].start()] if matches else wikitext
    sections: list[tuple[int, str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(wikitext)
        sections.append((len(m.group("eq")), m.group("title").strip(), wikitext[m.end() : end]))
    return lead, sections


def _collect(
    sections: list[tuple[int, str, str]], headings: tuple[str, ...]
) -> list[tuple[str, str]]:
    """Sections whose title matches, each with its subsections, as (title, body)."""
    out: list[tuple[str, str]] = []
    active_level: int | None = None
    for level, title, body in sections:
        lowered = title.lower()
        if any(h in lowered for h in headings):
            active_level = level
            out.append((title, body))
        elif active_level is not None and level > active_level:
            out.append((title, body))
        else:
            active_level = None
    return out


def _bullets(body: str) -> list[str]:
    lines = []
    for raw in body.splitlines():
        if raw.startswith("*"):
            cleaned = clean_wikitext(raw.lstrip("*").strip())
            if cleaned:
                lines.append(cleaned)
    return lines


def _table_rows(body: str) -> list[str]:
    """First cell of every data row of wikitables in the body (header rows skipped)."""
    rows: list[str] = []
    in_table = False
    current: list[str] = []

    def flush() -> None:
        if current:
            cleaned = clean_wikitext(current[0])
            if cleaned:
                rows.append(cleaned)
        current.clear()

    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("{|"):
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith("|}"):
            flush()
            in_table = False
        elif line.startswith("|-"):
            flush()
        elif line.startswith("|+") or line.startswith("!"):
            continue  # caption or header cell
        elif line.startswith("|"):
            current.extend(cell.strip() for cell in line[1:].split("||"))
    flush()
    return rows


def ingredient_lines(wikitext: str) -> list[str]:
    """Ingredient section lines: bullets, else wikitable rows; fallback: first bullet list."""
    _, sections = _sections(wikitext)
    lines: list[str] = []
    for _, body in _collect(sections, INGREDIENT_HEADINGS):
        lines.extend(_bullets(body) or _table_rows(body))
    if lines:
        return lines
    for _, title, body in sections:
        if any(h in title.lower() for h in METHOD_HEADINGS):
            break
        bullets = _bullets(body)
        if len(bullets) >= 3:
            return bullets
    return []


def method_text(wikitext: str) -> str:
    """Plain text of the procedure section(s); subsection headings are kept as lines."""
    _, sections = _sections(wikitext)
    chunks: list[str] = []
    collected = _collect(sections, METHOD_HEADINGS)
    for i, (title, body) in enumerate(collected):
        if i > 0:
            chunks.append(clean_wikitext(title) + ":")
        for raw in body.splitlines():
            stripped = re.sub(r"^[#*:;]+\s*", "", raw)
            cleaned = clean_wikitext(stripped)
            if cleaned:
                chunks.append(cleaned)
    return "\n".join(chunks)


def summary_text(wikitext: str) -> str:
    """Lead paragraph(s) before the first heading, cleaned; breadcrumb lines dropped."""
    lead, _ = _sections(wikitext)
    cleaned = clean_wikitext(lead)
    kept = [line for line in cleaned.splitlines() if line and not line.startswith("|")]
    return "\n".join(kept).strip()


def categories_from_wikitext(wikitext: str) -> list[str]:
    seen: list[str] = []
    for m in _CATEGORY_RE.finditer(wikitext):
        name = m.group(1).strip()
        if name not in seen:
            seen.append(name)
    return seen


# --- ids and diet ---------------------------------------------------------------------------


def slug(title: str) -> str:
    """ASCII slug of the title without the Cookbook: prefix (SPEC 2.2)."""
    bare = re.sub(r"^\s*Cookbook\s*:\s*", "", title)
    ascii_text = unicodedata.normalize("NFKD", bare).encode("ascii", "ignore").decode("ascii")
    out = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return out[:64].strip("-")


def _has_term(line: str, terms: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(term)}(?:es|s)?\b", line) for term in terms)


def strip_veto_exceptions(line: str) -> str:
    out = line.lower()
    for phrase in VETO_EXCEPTIONS:
        out = re.sub(rf"\b{re.escape(phrase)}\b", " ", out)
    return out


def diet_flags(categories: list[str], ingredients: list[str]) -> dict[str, str]:
    """SPEC 2.3: category gives yes, whole-word veto terms give no, otherwise unknown."""
    vegetarian = "unknown"
    vegan = "unknown"
    if DIET_CATEGORIES["vegan"] in categories:
        vegetarian = vegan = "yes"
    elif DIET_CATEGORIES["vegetarian"] in categories:
        vegetarian = "yes"
    lines = [strip_veto_exceptions(line) for line in ingredients]
    if any(_has_term(line, VEGETARIAN_VETO_TERMS) for line in lines):
        vegetarian = vegan = "no"
    elif any(_has_term(line, VEGAN_VETO_TERMS) for line in lines):
        vegan = "no"
    return {"vegetarian": vegetarian, "vegan": vegan}


# --- whole page -----------------------------------------------------------------------------


def parse_page(
    *,
    title: str,
    page_id: int,
    revision_id: int,
    url: str,
    categories: list[str],
    wikitext: str,
) -> Recipe:
    """Build the SPEC 2.2 record from API metadata and page text."""
    bare_title = re.sub(r"^\s*Cookbook\s*:\s*", "", title).strip()
    params = parse_summary_template(wikitext)
    total = parse_time_field(params["time"]) if params and "time" in params else None
    merged = list(categories)
    for c in categories_from_wikitext(wikitext):
        if c not in merged:
            merged.append(c)
    ingredients = ingredient_lines(wikitext)
    return Recipe(
        id=slug(bare_title),
        title=bare_title,
        url=url,
        page_id=page_id,
        revision_id=revision_id,
        categories=merged,
        has_summary_template=params is not None,
        total_minutes=total,
        diet=diet_flags(merged, ingredients),
        ingredients=ingredients,
        method=method_text(wikitext),
        summary=summary_text(wikitext),
    )


def corpus_version(recipes: list[dict[str, Any]]) -> str:
    return compute_version(recipes)
