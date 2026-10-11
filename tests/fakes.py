"""Deterministic test doubles: no network, no model (CLAUDE.md hard rule)."""

from __future__ import annotations

import hashlib
import re

import numpy as np

from recipe_qa.corpus import Recipe
from recipe_qa.llm import GenerationRequest, LLMOutput, LLMResult, LLMStatus

_TOKEN = re.compile(r"[a-z0-9]+")


def make_recipe(
    rid: str,
    title: str | None = None,
    *,
    minutes: int | None = 30,
    vegetarian: str = "unknown",
    vegan: str = "unknown",
    ingredients: list[str] | None = None,
    method: str = "Cook everything together and serve.",
    categories: list[str] | None = None,
    summary: str = "",
) -> Recipe:
    return Recipe(
        id=rid,
        title=title or rid.replace("-", " ").title(),
        url=f"https://en.wikibooks.org/wiki/Cookbook:{rid}",
        page_id=abs(hash(rid)) % 10_000_000,
        revision_id=1,
        categories=categories or [],
        has_summary_template=True,
        total_minutes=minutes,
        diet={"vegetarian": vegetarian, "vegan": vegan},  # type: ignore[dict-item]
        ingredients=ingredients if ingredients is not None else ["1 cup flour", "2 eggs"],
        method=method,
        summary=summary,
    )


class FakeEmbedder:
    """Bag-of-hashed-words vectors: texts sharing words get a high cosine. Deterministic."""

    model = "fake-embedder"
    dimension = 4096

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        if self.fail:
            raise ConnectionError("fake embedder is offline")
        out = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for i, text in enumerate(texts):
            for tok in _TOKEN.findall(text.lower()):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                out[i, h % self.dimension] += 1.0
            norm = np.linalg.norm(out[i])
            if norm:
                out[i] /= norm
        return out


class FakeLLM:
    """Scripted LLM: returns queued outputs or raises queued exceptions; records requests."""

    model = "fake-llm"

    def __init__(self, script: list[LLMOutput | Exception] | None = None) -> None:
        self.script = list(script or [])
        self.requests: list[GenerationRequest] = []
        self.calls = 0

    def generate(self, request: GenerationRequest) -> LLMResult:
        self.calls += 1
        self.requests.append(request)
        item: LLMOutput | Exception | None = self.script.pop(0) if self.script else None
        if isinstance(item, Exception):
            raise item
        if item is None:  # default: answer citing the first allowed recipe
            item = LLMOutput(
                status=LLMStatus.ANSWERED,
                answer="A grounded answer from the fake model.",
                cited_ids=request.allowed_ids[:1],
            )
        return LLMResult(
            output=item, model=self.model, input_tokens=120, output_tokens=40, attempts=1
        )


def answered(cited_ids: list[str], answer: str = "Grounded answer.") -> LLMOutput:
    return LLMOutput(status=LLMStatus.ANSWERED, answer=answer, cited_ids=cited_ids)


def refusal(status: LLMStatus) -> LLMOutput:
    return LLMOutput(status=status, answer="", cited_ids=[])
