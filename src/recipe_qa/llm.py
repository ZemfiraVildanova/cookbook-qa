"""LLM client interface and the structured-output contract (SPEC 5.6, R-41, R-50, R-61).

Provider adapters (Anthropic, OpenAI) implement `LLMClient`; the pipeline only sees
`GenerationRequest` in and `LLMResult` out. Output is constrained to `LLMOutput` with the
citation ids restricted to the retrieved set (R-02) and validated again on the server (R-41).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from recipe_qa.config import LLM_MAX_RETRIES, LLM_RETRY_BACKOFF_S


class LLMStatus(StrEnum):
    ANSWERED = "answered"
    OUT_OF_CORPUS = "out_of_corpus"
    OUT_OF_DOMAIN = "out_of_domain"
    SAFETY = "safety"


class LLMError(RuntimeError):
    """Base class; `code` is what the 503 body carries (SPEC 3.4)."""

    code = "llm_error"
    attempts = 0  # set by with_retry when every attempt failed, for the log line (R-70)


class LLMUnavailableError(LLMError):
    code = "llm_unavailable"


class LLMInvalidOutputError(LLMError):
    code = "llm_invalid_output"


class LLMOutput(BaseModel):
    """What the model must return (SPEC 4 step 6)."""

    model_config = ConfigDict(extra="forbid")

    status: LLMStatus
    answer: str
    cited_ids: list[str]


def output_schema(allowed_ids: list[str]) -> dict[str, Any]:
    """JSON Schema for structured output; `cited_ids` is an enum of the retrieved ids (R-02).

    The pipeline never calls the model with an empty retrieved set (R-19), so the enum is
    never empty.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "answer", "cited_ids"],
        "properties": {
            "status": {"type": "string", "enum": [s.value for s in LLMStatus]},
            "answer": {"type": "string"},
            "cited_ids": {"type": "array", "items": {"type": "string", "enum": list(allowed_ids)}},
        },
    }


def parse_output(text: str) -> LLMOutput:
    """Validate raw model text against the output contract (R-41)."""
    try:
        return LLMOutput.model_validate_json(text)
    except ValidationError as exc:
        raise LLMInvalidOutputError(
            f"model output does not match the schema: {exc.error_count()} errors"
        ) from exc


@dataclass(frozen=True)
class GenerationRequest:
    system: str
    user: str
    allowed_ids: list[str]


@dataclass(frozen=True)
class LLMResult:
    output: LLMOutput
    model: str
    input_tokens: int
    output_tokens: int
    attempts: int


class LLMClient(Protocol):
    model: str

    def generate(self, request: GenerationRequest) -> LLMResult: ...


def with_retry[T](
    attempt: Callable[[], T],
    *,
    retries: int = LLM_MAX_RETRIES,
    backoff_s: float = LLM_RETRY_BACKOFF_S,
) -> tuple[T, int]:
    """Run `attempt` up to retries + 1 times on LLMError; returns (result, attempts)."""
    last: LLMError | None = None
    for n in range(1, retries + 2):
        try:
            return attempt(), n
        except LLMError as exc:
            last = exc
            if n <= retries:
                time.sleep(backoff_s)
    assert last is not None
    last.attempts = retries + 1
    raise last
