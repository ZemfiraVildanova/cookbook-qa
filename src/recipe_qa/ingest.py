"""Wikitext parsing and corpus metadata (SPEC 2.2, 2.3, 2.4). Implemented in the next commit."""

from __future__ import annotations

from typing import Any


def parse_duration(text: str) -> int | None:
    raise NotImplementedError


def parse_time_field(value: str) -> int | None:
    raise NotImplementedError


def parse_summary_template(wikitext: str) -> dict[str, str] | None:
    raise NotImplementedError


def ingredient_lines(wikitext: str) -> list[str]:
    raise NotImplementedError


def method_text(wikitext: str) -> str:
    raise NotImplementedError


def summary_text(wikitext: str) -> str:
    raise NotImplementedError


def categories_from_wikitext(wikitext: str) -> list[str]:
    raise NotImplementedError


def slug(title: str) -> str:
    raise NotImplementedError


def diet_flags(categories: list[str], ingredients: list[str]) -> dict[str, str]:
    raise NotImplementedError


def parse_page(
    *,
    title: str,
    page_id: int,
    revision_id: int,
    url: str,
    categories: list[str],
    wikitext: str,
) -> Any:
    raise NotImplementedError


def corpus_version(recipes: list[dict[str, Any]]) -> str:
    raise NotImplementedError
