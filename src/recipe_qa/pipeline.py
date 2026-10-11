"""The /ask pipeline (SPEC 4): parse, safety gate, retrieve, pre-model exits, generate, guards, log.

Every outcome of a request is produced here; `app.py` only maps exceptions to HTTP codes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from recipe_qa import request_log
from recipe_qa.config import PROMPT_VERSION, REFUSAL_TEMPLATES
from recipe_qa.constraints import IngredientIndex, parse_constraints
from recipe_qa.corpus import Corpus, Recipe
from recipe_qa.llm import LLMClient, LLMError, LLMResult, LLMStatus, LLMUnavailableError
from recipe_qa.prompting import build_request
from recipe_qa.retrieval import Embedder, Index, RetrievalResult
from recipe_qa.safety import check_safety, safety_answer
from recipe_qa.schemas import (
    AppliedConstraints,
    AskResponse,
    Citation,
    Meta,
    RefusalReason,
    RetrievalMode,
    Usage,
)

TOP_RANKED_LOGGED = 10  # fused candidates kept in the log line for "why not X" questions


@dataclass
class Deps:
    corpus: Corpus
    index: Index
    embedder: Embedder | None
    llm: LLMClient | None


class _TimedEmbedder:
    """Wraps an embedder to measure the query embedding time for the log line."""

    def __init__(self, inner: Embedder) -> None:
        self.inner = inner
        self.model = inner.model
        self.dimension = inner.dimension
        self.last_ms = 0

    def embed(self, texts: list[str]) -> np.ndarray:
        started = time.perf_counter()
        try:
            return self.inner.embed(texts)
        finally:
            self.last_ms = int((time.perf_counter() - started) * 1000)


@dataclass
class _Trace:
    """Everything the log line needs, filled as the request moves through the stages."""

    request_id: str
    question: str
    constraints: AppliedConstraints | None = None
    safety_pattern: str | None = None
    retrieval: RetrievalResult | None = None
    llm: dict[str, Any] = field(
        default_factory=lambda: {
            "provider": None,
            "model": None,
            "prompt_version": None,
            "attempts": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "status": None,
        }
    )
    outcome: dict[str, Any] = field(
        default_factory=lambda: {"refused": None, "refusal_reason": None, "cited_ids": []}
    )
    latency_ms: dict[str, int] = field(
        default_factory=lambda: {"parse": 0, "embed": 0, "retrieve": 0, "llm": 0, "total": 0}
    )
    http_status: int = 200


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _citations(recipes: list[Recipe]) -> list[Citation]:
    return [Citation(recipe_id=r.id, title=r.title, url=r.url) for r in recipes]


class Pipeline:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.by_id = deps.corpus.by_id()
        self.ingredient_index = IngredientIndex(
            line for recipe in deps.corpus.recipes for line in recipe.ingredients
        )
        self.embedder = _TimedEmbedder(deps.embedder) if deps.embedder is not None else None

    # --- public ------------------------------------------------------------------------------

    def ask(self, question: str, request_id: str) -> AskResponse:
        """SPEC 4 steps 2 to 8. Raises LLMError (→ 503) or any unexpected error (→ 500)."""
        started = time.perf_counter()
        trace = _Trace(request_id=request_id, question=question)
        try:
            response = self._run(question, trace)
            trace.outcome = {
                "refused": response.refused,
                "refusal_reason": response.refusal_reason.value
                if response.refusal_reason
                else None,
                "cited_ids": [c.recipe_id for c in response.citations],
            }
            return response
        except LLMError as exc:
            trace.http_status = 503
            trace.llm["status"] = exc.code
            raise
        except Exception:
            trace.http_status = 500
            raise
        finally:
            trace.latency_ms["total"] = _ms(started)
            self._log(trace)

    # --- stages --------------------------------------------------------------------------------

    def _run(self, question: str, trace: _Trace) -> AskResponse:
        t = time.perf_counter()
        constraints = parse_constraints(question, self.ingredient_index)
        trace.constraints = constraints
        trace.safety_pattern = check_safety(question)
        trace.latency_ms["parse"] = _ms(t)

        t = time.perf_counter()
        retrieval = self.deps.index.retrieve(question, constraints, self.embedder)
        trace.retrieval = retrieval
        trace.latency_ms["retrieve"] = _ms(t)
        trace.latency_ms["embed"] = self.embedder.last_ms if self.embedder else 0
        retrieved = retrieval.retrieved

        if trace.safety_pattern is not None:  # R-30, R-35: $0 exit, wins over out_of_corpus
            return self._safety(retrieved, constraints, retrieval.mode, trace, llm=None)
        if not retrieved:  # R-19
            return self._refusal(
                RefusalReason.OUT_OF_CORPUS, constraints, retrieval.mode, trace, None
            )

        if self.deps.llm is None:
            raise LLMUnavailableError("no generation model is configured")
        request = build_request(question, constraints, retrieved)
        t = time.perf_counter()
        trace.llm.update(model=self.deps.llm.model, prompt_version=PROMPT_VERSION)
        try:
            result = self.deps.llm.generate(request)
        finally:
            trace.latency_ms["llm"] = _ms(t)
        trace.llm.update(
            attempts=result.attempts,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            status=result.output.status.value,
        )
        output = result.output
        allowed = [r.id for r in retrieved]
        cited_ids: list[str] = []
        for rid in output.cited_ids:  # R-02, R-05
            if rid in allowed and rid not in cited_ids:
                cited_ids.append(rid)

        if output.status is LLMStatus.SAFETY:  # R-34
            return self._safety(retrieved, constraints, retrieval.mode, trace, llm=result)
        if output.status is LLMStatus.OUT_OF_CORPUS:
            return self._refusal(
                RefusalReason.OUT_OF_CORPUS, constraints, retrieval.mode, trace, result
            )
        if output.status is LLMStatus.OUT_OF_DOMAIN:
            return self._refusal(
                RefusalReason.OUT_OF_DOMAIN, constraints, retrieval.mode, trace, result
            )
        if not cited_ids or not output.answer.strip():  # R-04, E-22
            return self._refusal(
                RefusalReason.OUT_OF_CORPUS, constraints, retrieval.mode, trace, result
            )
        return AskResponse(
            answer=output.answer.strip(),
            citations=_citations([self.by_id[rid] for rid in cited_ids]),
            refused=False,
            refusal_reason=None,
            applied_constraints=constraints,
            meta=self._meta(trace, retrieval.mode, result),
        )

    # --- response builders ----------------------------------------------------------------------

    def _meta(self, trace: _Trace, mode: RetrievalMode, llm: LLMResult | None) -> Meta:
        return Meta(
            request_id=trace.request_id,
            model=llm.model if llm else None,
            prompt_version=PROMPT_VERSION if llm else None,
            corpus_version=self.deps.corpus.corpus_version,
            retrieval_mode=mode,
            latency_ms=_ms_total(trace),
            usage=Usage(
                input_tokens=llm.input_tokens if llm else 0,
                output_tokens=llm.output_tokens if llm else 0,
            ),
        )

    def _safety(
        self,
        retrieved: list[Recipe],
        constraints: AppliedConstraints,
        mode: RetrievalMode,
        trace: _Trace,
        llm: LLMResult | None,
    ) -> AskResponse:
        return AskResponse(
            answer=safety_answer(retrieved),
            citations=_citations(retrieved),
            refused=True,
            refusal_reason=RefusalReason.SAFETY,
            applied_constraints=constraints,
            meta=self._meta(trace, mode, llm),
        )

    def _refusal(
        self,
        reason: RefusalReason,
        constraints: AppliedConstraints,
        mode: RetrievalMode,
        trace: _Trace,
        llm: LLMResult | None,
    ) -> AskResponse:
        return AskResponse(
            answer=REFUSAL_TEMPLATES[reason.value],
            citations=[],
            refused=True,
            refusal_reason=reason,
            applied_constraints=constraints,
            meta=self._meta(trace, mode, llm),
        )

    # --- log line (R-70) --------------------------------------------------------------------------

    def _log(self, trace: _Trace) -> None:
        retrieval = trace.retrieval
        retrieved_ids = [r.id for r in retrieval.retrieved] if retrieval else []
        by_id = {c.id: c for c in retrieval.ranked} if retrieval else {}

        def candidate(rid: str) -> dict[str, Any]:
            c = by_id[rid]
            return {
                "id": c.id,
                "bm25_rank": c.bm25_rank,
                "vec_rank": c.vec_rank,
                "bm25_score": round(c.bm25_score, 4),
                "cosine": round(c.cosine, 4) if c.cosine is not None else None,
                "rrf": round(c.rrf, 6),
            }

        request_log.emit(
            {
                "request_id": trace.request_id,
                "http_status": trace.http_status,
                "question": trace.question,
                "question_chars": len(trace.question),
                "constraints": trace.constraints.model_dump(mode="json")
                if trace.constraints
                else None,
                "safety_pattern": trace.safety_pattern,
                "retrieval_mode": retrieval.mode.value if retrieval else None,
                "retrieved": [candidate(rid) for rid in retrieved_ids],
                "top_ranked": [
                    candidate(c.id)
                    for c in (retrieval.ranked[:TOP_RANKED_LOGGED] if retrieval else [])
                ],
                "filtered_out": retrieval.filtered_out if retrieval else None,
                "llm": trace.llm,
                "outcome": trace.outcome,
                "latency_ms": trace.latency_ms,
            }
        )


def _ms_total(trace: _Trace) -> int:
    """Latency so far; the final total goes to the log line, the response gets the same value."""
    return sum(v for k, v in trace.latency_ms.items() if k != "total")
