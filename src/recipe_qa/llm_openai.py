"""OpenAI adapter for `LLMClient` (SPEC 5.6, R-50, R-54, R-61; ADR-003).

One `POST /v1/responses` call over HTTP (httpx, no SDK): the JSON Schema from
`llm.output_schema` goes in `text.format` with `strict: true`, so `cited_ids` is constrained
to the retrieved ids at generation time; `reasoning.effort` comes from config so no hidden
reasoning tokens are billed or waited for; `store: false` keeps the request out of the
vendor's storage. Error mapping follows SPEC 3.4; the single retry is `with_retry`.
"""

from __future__ import annotations

from typing import Any

import httpx

from recipe_qa.config import (
    LLM_MAX_OUTPUT_TOKENS,
    LLM_REASONING_EFFORT,
    LLM_TIMEOUT_S,
    OPENAI_API_BASE,
)
from recipe_qa.llm import (
    GenerationRequest,
    LLMInvalidOutputError,
    LLMOutput,
    LLMResult,
    LLMUnavailableError,
    output_schema,
    parse_output,
    with_retry,
)


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
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def _body(self, request: GenerationRequest) -> dict[str, Any]:
        return {
            "model": self.model,
            "instructions": request.system,
            "input": request.user,
            "max_output_tokens": LLM_MAX_OUTPUT_TOKENS,
            "reasoning": {"effort": LLM_REASONING_EFFORT},  # R-50
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "recipe_answer",
                    "strict": True,
                    "schema": output_schema(request.allowed_ids),
                }
            },
        }

    def generate(self, request: GenerationRequest) -> LLMResult:
        def attempt() -> tuple[LLMOutput, int, int]:
            try:
                response = self._client.post("/responses", json=self._body(request))
            except httpx.TimeoutException as exc:
                raise LLMUnavailableError("timeout") from exc
            except httpx.HTTPError as exc:
                raise LLMUnavailableError(type(exc).__name__) from exc
            if response.status_code != 200:
                raise LLMUnavailableError(f"HTTP {response.status_code}: {response.text[:200]}")
            data = response.json()
            if data.get("status") != "completed":
                reason = (data.get("incomplete_details") or {}).get("reason")
                raise LLMInvalidOutputError(f"response {data.get('status')}: {reason}")
            text: str | None = None
            for item in data.get("output", []):
                if item.get("type") != "message":
                    continue
                for part in item.get("content", []):
                    if part.get("type") == "refusal":
                        raise LLMInvalidOutputError("model returned a refusal item")
                    if part.get("type") == "output_text":
                        text = str(part.get("text", ""))
            if text is None:
                raise LLMInvalidOutputError("no output_text item in the response")
            usage = data.get("usage") or {}
            return (
                parse_output(text),
                int(usage.get("input_tokens", 0)),
                int(usage.get("output_tokens", 0)),
            )

        (output, input_tokens, output_tokens), attempts = with_retry(attempt)
        return LLMResult(
            output=output,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            attempts=attempts,
        )
