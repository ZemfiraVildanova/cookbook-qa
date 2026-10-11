"""Structured-output contract and retry helper (SPEC 4 step 6, R-02, R-41, R-50). No network."""

from __future__ import annotations

import pytest

from recipe_qa import llm


def test_output_schema_restricts_cited_ids() -> None:
    """R-02: cited_ids is an enum of the retrieved ids; the object is closed."""
    schema = llm.output_schema(["a", "b"])
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"status", "answer", "cited_ids"}
    assert schema["properties"]["cited_ids"]["items"]["enum"] == ["a", "b"]
    assert set(schema["properties"]["status"]["enum"]) == {
        "answered",
        "out_of_corpus",
        "out_of_domain",
        "safety",
    }


def test_parse_output_accepts_valid_and_rejects_invalid() -> None:
    """R-41: server-side validation regardless of provider guarantees."""
    out = llm.parse_output('{"status": "answered", "answer": "x", "cited_ids": ["a"]}')
    assert out.status is llm.LLMStatus.ANSWERED and out.cited_ids == ["a"]
    for bad in (
        "not json",
        '{"status": "maybe", "answer": "x", "cited_ids": []}',
        '{"status": "answered", "answer": "x"}',
        '{"status": "answered", "answer": "x", "cited_ids": [], "extra": 1}',
    ):
        with pytest.raises(llm.LLMInvalidOutputError):
            llm.parse_output(bad)


def test_with_retry_retries_once_then_raises() -> None:
    """R-50: one retry on an LLMError, attempts are counted."""
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise llm.LLMUnavailableError("first try fails")
        return "ok"

    value, attempts = llm.with_retry(flaky, backoff_s=0)
    assert value == "ok" and attempts == 2

    def always() -> str:
        raise llm.LLMInvalidOutputError("never valid")

    with pytest.raises(llm.LLMInvalidOutputError):
        llm.with_retry(always, backoff_s=0)


def test_anthropic_request_disables_thinking() -> None:
    """R-50 / LLM_THINKING: the Anthropic request carries thinking={"type": LLM_THINKING}."""
    from types import SimpleNamespace

    from recipe_qa.config import LLM_MAX_OUTPUT_TOKENS, LLM_THINKING
    from recipe_qa.llm import GenerationRequest
    from recipe_qa.llm_anthropic import AnthropicClient

    seen: dict[str, object] = {}

    def create(**kwargs: object) -> SimpleNamespace:
        seen.update(kwargs)
        text = '{"status": "answered", "answer": "ok", "cited_ids": ["r1"]}'
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

    client = AnthropicClient("test-key", "test-model")
    client._client = SimpleNamespace(messages=SimpleNamespace(create=create))  # type: ignore[assignment]
    result = client.generate(GenerationRequest(system="s", user="u", allowed_ids=["r1"]))
    assert result.output.cited_ids == ["r1"] and result.attempts == 1
    assert seen["thinking"] == {"type": LLM_THINKING}
    assert seen["max_tokens"] == LLM_MAX_OUTPUT_TOKENS
    assert "temperature" not in seen
