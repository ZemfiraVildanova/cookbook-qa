"""OpenAI adapter for `LLMClient` (SPEC 5.6, R-50, R-54; ADR-003). Implemented next commit."""

from __future__ import annotations

import httpx

from recipe_qa.config import LLM_TIMEOUT_S, OPENAI_API_BASE
from recipe_qa.llm import GenerationRequest, LLMResult


class OpenAIClient:
    provider = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float = LLM_TIMEOUT_S,
        base_url: str = OPENAI_API_BASE,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        raise NotImplementedError

    def generate(self, request: GenerationRequest) -> LLMResult:
        raise NotImplementedError
