"""Structured-output contract and retry helper (SPEC 4 step 6, R-02, R-41, R-50). No network."""

from __future__ import annotations

import json

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


def _openai_body(text: str) -> dict[str, object]:
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def test_openai_request_and_response_shape() -> None:
    """R-50, R-54: /responses with a strict JSON schema, reasoning off, store off, no sampling."""
    import httpx

    from recipe_qa.config import LLM_MAX_OUTPUT_TOKENS, LLM_REASONING_EFFORT
    from recipe_qa.llm import GenerationRequest
    from recipe_qa.llm_openai import OpenAIClient

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization", "")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json=_openai_body('{"status": "answered", "answer": "ok", "cited_ids": ["r1"]}')
        )

    client = OpenAIClient("test-key", "test-model", transport=httpx.MockTransport(handler))
    result = client.generate(GenerationRequest(system="s", user="u", allowed_ids=["r1"]))
    assert result.output.cited_ids == ["r1"] and result.attempts == 1
    assert result.input_tokens == 10 and result.output_tokens == 5
    assert str(seen["url"]).endswith("/responses") and seen["auth"] == "Bearer test-key"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "test-model" and body["instructions"] == "s" and body["input"] == "u"
    assert body["max_output_tokens"] == LLM_MAX_OUTPUT_TOKENS
    assert body["reasoning"] == {"effort": LLM_REASONING_EFFORT}
    assert body["store"] is False and "temperature" not in body
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["name"]
    assert fmt["schema"]["properties"]["cited_ids"]["items"]["enum"] == ["r1"]


@pytest.mark.xfail(strict=True, reason="implemented in next commit")
def test_openai_errors_map_to_spec_codes() -> None:
    """3.4: truncated or refused output is llm_invalid_output; 5xx is llm_unavailable."""
    import httpx

    from recipe_qa.llm import GenerationRequest, LLMInvalidOutputError, LLMUnavailableError
    from recipe_qa.llm_openai import OpenAIClient

    req = GenerationRequest(system="s", user="u", allowed_ids=["r1"])
    incomplete = {
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [],
        "usage": {"input_tokens": 10, "output_tokens": 1000},
    }
    refusal = {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}],
        "usage": {"input_tokens": 10, "output_tokens": 3},
    }
    for canned in (incomplete, refusal):
        transport = httpx.MockTransport(lambda _r, c=canned: httpx.Response(200, json=c))
        with pytest.raises(LLMInvalidOutputError):
            OpenAIClient("k", "m", transport=transport).generate(req)
    failed = {"status": "failed", "error": {"code": "server_error"}, "output": [], "usage": {}}
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, json=failed))
    with pytest.raises(LLMUnavailableError):  # 3.4: a failed response is an API failure, retried
        OpenAIClient("k", "m", transport=transport).generate(req)
    calls = 0

    def down(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": {"message": "down"}})

    with pytest.raises(LLMUnavailableError) as info:
        OpenAIClient("k", "m", transport=httpx.MockTransport(down)).generate(req)
    assert calls == 2 and info.value.attempts == 2


def test_make_llm_client_selects_provider() -> None:
    """R-54: provider and model come from settings; a missing key means no client."""
    from pydantic import SecretStr

    from recipe_qa.app import make_llm_client
    from recipe_qa.config import Settings

    settings = Settings(  # type: ignore[call-arg]
        llm_provider="openai", llm_model="m", openai_api_key=SecretStr("k"), _env_file=None
    )
    client = make_llm_client(settings)
    assert client is not None and client.model == "m"
    assert getattr(client, "provider", None) == "openai"
    assert make_llm_client(Settings(llm_provider="openai", _env_file=None)) is None  # type: ignore[call-arg]
