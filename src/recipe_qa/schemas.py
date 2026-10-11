"""The API contract, defined once (SPEC section 3).

OpenAPI, the LLM output schema, the eval harness and the frontend types derive from the
models in this module. Field names, enums and nullability match SPEC 3.3 exactly.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from recipe_qa.config import QUESTION_MAX_CHARS, QUESTION_MIN_CHARS


class RefusalReason(StrEnum):
    OUT_OF_CORPUS = "out_of_corpus"
    OUT_OF_DOMAIN = "out_of_domain"
    SAFETY = "safety"


class RetrievalMode(StrEnum):
    HYBRID = "hybrid"
    BM25_ONLY = "bm25_only"
    NONE = "none"


class Diet(StrEnum):
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"


class AskRequest(BaseModel):
    """SPEC 3.2: one question, 1 to 500 characters after strip, no extra fields."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        description="Natural-language question about recipes in the corpus.",
        examples=["What's a vegetarian dinner I can make in under 30 minutes?"],
    )

    @field_validator("question")
    @classmethod
    def _strip_and_bound(cls, value: str) -> str:
        stripped = value.strip()
        if not QUESTION_MIN_CHARS <= len(stripped) <= QUESTION_MAX_CHARS:
            msg = (
                f"question must be {QUESTION_MIN_CHARS} to {QUESTION_MAX_CHARS} "
                "characters after trimming"
            )
            raise ValueError(msg)
        return stripped


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    title: str
    url: str = Field(json_schema_extra={"format": "uri"})


class AppliedConstraints(BaseModel):
    """SPEC 5.2: what the parser extracted and the filters enforced."""

    model_config = ConfigDict(extra="forbid")

    max_minutes: int | None = Field(default=None, ge=0)
    diet: Diet | None = None
    include_ingredients: list[str] = Field(default_factory=list)
    exclude_ingredients: list[str] = Field(default_factory=list)


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    model: str | None
    prompt_version: str | None
    corpus_version: str
    retrieval_mode: RetrievalMode
    latency_ms: int = Field(ge=0)
    usage: Usage


class AskResponse(BaseModel):
    """SPEC 3.3 response body. Invariants I-01..I-04 and I-08 are enforced here."""

    model_config = ConfigDict(extra="forbid")

    answer: str | None
    citations: list[Citation]
    refused: bool
    refusal_reason: RefusalReason | None
    applied_constraints: AppliedConstraints
    meta: Meta

    @model_validator(mode="after")
    def _invariants(self) -> AskResponse:
        if not self.answer or not self.answer.strip():
            raise ValueError("I-03: answer must be a non-empty string")
        if self.refused:
            if self.refusal_reason is None:
                raise ValueError("I-02: refused responses carry a refusal_reason")
            if self.refusal_reason is not RefusalReason.SAFETY and self.citations:
                raise ValueError("I-04: out_of_corpus and out_of_domain carry no citations")
        else:
            if self.refusal_reason is not None:
                raise ValueError("I-01: answered responses carry no refusal_reason")
            if not self.citations:
                raise ValueError("I-01: answered responses carry at least one citation")
        ids = [c.recipe_id for c in self.citations]
        if len(ids) != len(set(ids)):
            raise ValueError("I-05: citations are distinct by recipe_id")
        no_model = self.meta.model is None
        if no_model != (self.meta.prompt_version is None) or (
            no_model and (self.meta.usage.input_tokens or self.meta.usage.output_tokens)
        ):
            raise ValueError("I-08: model, prompt_version and usage agree on whether a model ran")
        return self


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    git_sha: str
    corpus_version: str


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    """SPEC 3.4 body for 503 and 500."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorBody
