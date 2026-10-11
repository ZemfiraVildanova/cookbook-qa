"""Anthropic adapter for `LLMClient` (SPEC 5.6, R-41, R-50, R-61; ADR-003).

One `messages.create` call with structured output: `output_config.format` carries the JSON
Schema from `llm.output_schema`, so `cited_ids` is constrained to the retrieved ids at
generation time. Sampling parameters are not sent (current Anthropic models reject them);
determinism comes from the schema and the fixed prompt. SDK retries are disabled so that
the single retry of SPEC R-50 is counted and logged by `with_retry`.
"""

from __future__ import annotations

import anthropic

from recipe_qa.config import LLM_MAX_OUTPUT_TOKENS, LLM_TIMEOUT_S
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


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, api_key: str, model: str, *, timeout: float = LLM_TIMEOUT_S) -> None:
        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)

    def generate(self, request: GenerationRequest) -> LLMResult:
        def attempt() -> tuple[LLMOutput, int, int]:
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=LLM_MAX_OUTPUT_TOKENS,
                    system=request.system,
                    messages=[{"role": "user", "content": request.user}],
                    output_config={
                        "format": {
                            "type": "json_schema",
                            "schema": output_schema(request.allowed_ids),
                        }
                    },
                )
            except (
                anthropic.APITimeoutError,
                anthropic.APIConnectionError,
                anthropic.RateLimitError,
                anthropic.InternalServerError,
            ) as exc:
                raise LLMUnavailableError(f"{type(exc).__name__}") from exc
            except anthropic.APIStatusError as exc:
                raise LLMUnavailableError(f"HTTP {exc.status_code}: {exc.message[:200]}") from exc
            if response.stop_reason == "refusal":
                raise LLMInvalidOutputError("model stopped with a refusal")
            text = next((b.text for b in response.content if b.type == "text"), "")
            if response.stop_reason == "max_tokens":
                raise LLMInvalidOutputError("output truncated at max_tokens")
            output = parse_output(text)
            return output, response.usage.input_tokens, response.usage.output_tokens

        (output, input_tokens, output_tokens), attempts = with_retry(attempt)
        return LLMResult(
            output=output,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            attempts=attempts,
        )
