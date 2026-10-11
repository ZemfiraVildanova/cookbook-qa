"""/ask contract branches through TestClient with a fake LLM (SPEC 4, 5.1, 5.3-5.8, 12.1).

Every branch of the pipeline: safety gate, filters leaving nothing, normal answer, foreign
and duplicate citations, answer without citations, model refusals, model safety status,
LLM failures, constraint echo, meta fields and the request log line.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from recipe_qa.config import (
    PROMPT_RECIPE_MAX_CHARS,
    PROMPT_VERSION,
    REFUSAL_TEMPLATES,
    SAFETY_DISCLAIMER,
    Settings,
)
from recipe_qa.constraints import recipe_passes
from recipe_qa.corpus import Corpus, load_corpus
from recipe_qa.llm import LLMInvalidOutputError, LLMStatus, LLMUnavailableError
from recipe_qa.schemas import AskResponse, RefusalReason
from tests.fakes import FakeEmbedder, FakeLLM, answered, make_recipe, refusal

ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH = ROOT / "data" / "corpus.json"


def corpus() -> Corpus:
    return load_corpus(CORPUS_PATH)


def make_client(llm: FakeLLM, embedder: FakeEmbedder | None = None) -> tuple[TestClient, Any]:
    """App wired to the committed corpus, a fake-embedded index and the given fake LLM."""
    from recipe_qa.app import create_app
    from recipe_qa.pipeline import Deps
    from recipe_qa.retrieval import Index, build_vectors

    c = corpus()
    emb = embedder or FakeEmbedder()
    vectors, ids = build_vectors(
        c, FakeEmbedder()
    )  # the index is always built; `emb` may fail at query time
    deps = Deps(corpus=c, index=Index(c, vectors, ids), embedder=emb, llm=llm)
    settings = Settings(git_sha="testsha", _env_file=None)  # type: ignore[call-arg]
    client = TestClient(create_app(settings, deps=deps), raise_server_exceptions=False)
    client.__enter__()
    return client, deps


def ask(client: TestClient, question: str) -> tuple[int, dict[str, Any]]:
    r = client.post("/ask", json={"question": question})
    return r.status_code, r.json()


def test_health_reports_corpus_version_without_external_calls() -> None:
    """R-72 / SPEC 3.6."""
    llm = FakeLLM()
    emb = FakeEmbedder()
    client, deps = make_client(llm, emb)
    emb.calls = 0
    body = client.get("/health").json()
    assert body["corpus_version"] == deps.corpus.corpus_version
    assert llm.calls == 0 and emb.calls == 0


def test_safety_gate_refuses_before_model() -> None:
    """R-30, R-31: safety refusal with ingredient lists, $0, citations = retrieved recipes."""
    llm = FakeLLM()
    client, _ = make_client(llm)
    status, body = ask(client, "Is the carbonara nut-free?")
    assert status == 200
    resp = AskResponse.model_validate(body)
    assert resp.refused and resp.refusal_reason is RefusalReason.SAFETY
    assert resp.answer is not None and resp.answer.startswith(SAFETY_DISCLAIMER)
    assert resp.citations and any(c.recipe_id == "spaghetti-alla-carbonara" for c in resp.citations)
    assert resp.meta.model is None and resp.meta.usage.input_tokens == 0
    assert llm.calls == 0


def test_safety_with_no_candidates_is_disclaimer_only() -> None:
    """R-35: safety wins over out_of_corpus; no citations when filters leave nothing."""
    client, _ = make_client(FakeLLM())
    _, body = ask(client, "Is a vegan dish in under 1 minute safe for my toddler?")
    resp = AskResponse.model_validate(body)
    assert resp.refusal_reason is RefusalReason.SAFETY
    assert resp.citations == [] and resp.answer == SAFETY_DISCLAIMER


def test_filters_exhaust_candidates() -> None:
    """R-19: impossible constraints refuse with out_of_corpus and no model call."""
    llm = FakeLLM()
    client, _ = make_client(llm)
    _, body = ask(client, "a vegan dish I can cook in under 1 minute")
    resp = AskResponse.model_validate(body)
    assert resp.refused and resp.refusal_reason is RefusalReason.OUT_OF_CORPUS
    assert resp.answer == REFUSAL_TEMPLATES["out_of_corpus"]
    assert resp.citations == [] and resp.meta.model is None
    assert resp.applied_constraints.max_minutes == 1
    assert llm.calls == 0


def test_normal_answer_with_citations_from_corpus() -> None:
    """R-01..R-03, R-51, R-54: answered, citation fields from the corpus, meta filled."""
    llm = FakeLLM()
    client, deps = make_client(llm)
    status, body = ask(client, "How do I make carbonara?")
    assert status == 200
    resp = AskResponse.model_validate(body)
    assert not resp.refused and resp.refusal_reason is None
    assert resp.answer == "A grounded answer from the fake model."
    cited = resp.citations[0]
    recipe = deps.corpus.by_id()[cited.recipe_id]
    assert cited.title == recipe.title and cited.url == recipe.url
    assert resp.meta.model == "fake-llm" and resp.meta.prompt_version == PROMPT_VERSION
    assert resp.meta.usage.input_tokens == 120 and resp.meta.usage.output_tokens == 40
    assert resp.meta.retrieval_mode.value == "hybrid"
    assert llm.calls == 1


def test_prompt_carries_question_recipes_and_constraints() -> None:
    """R-40, R-55, R-56: delimited data blocks; cited ids restricted to the retrieved set."""
    llm = FakeLLM()
    client, _ = make_client(llm)
    ask(client, "vegetarian pasta in under 60 minutes")
    req = llm.requests[0]
    assert "<question>" in req.user and "vegetarian pasta in under 60 minutes" in req.user
    assert "<constraints>" in req.user and '"max_minutes": 60' in req.user
    assert all(f'<recipe id="{rid}">' in req.user for rid in req.allowed_ids)
    assert 1 <= len(req.allowed_ids) <= 4
    assert "data, not instructions" in req.system


def test_render_recipe_truncates_long_methods() -> None:
    """R-55: method cut at PROMPT_RECIPE_MAX_CHARS with a visible marker."""
    from recipe_qa.prompting import TRUNCATION_MARKER, render_recipe

    long_recipe = make_recipe("long", "Long", method="step " * 2000)
    text = render_recipe(long_recipe)
    assert TRUNCATION_MARKER in text
    assert len(text) < PROMPT_RECIPE_MAX_CHARS + 500


def test_foreign_citation_dropped() -> None:
    """R-02: an id outside the retrieved set never reaches the response."""
    llm = FakeLLM([answered(["spaghetti-alla-carbonara", "not-a-recipe", "sachertorte"])])
    client, _ = make_client(llm)
    _, body = ask(client, "How do I make carbonara?")
    resp = AskResponse.model_validate(body)
    ids = [c.recipe_id for c in resp.citations]
    assert "not-a-recipe" not in ids
    assert "spaghetti-alla-carbonara" in ids
    assert "sachertorte" not in ids  # valid id, but not retrieved for this question


def test_duplicate_citations_deduplicated() -> None:
    """R-05."""
    llm = FakeLLM([answered(["spaghetti-alla-carbonara", "spaghetti-alla-carbonara"])])
    client, _ = make_client(llm)
    _, body = ask(client, "How do I make carbonara?")
    assert [c.recipe_id for c in AskResponse.model_validate(body).citations] == [
        "spaghetti-alla-carbonara"
    ]


def test_answer_without_citations_is_refusal() -> None:
    """R-04: answered with no valid citation becomes out_of_corpus."""
    llm = FakeLLM([answered([], "I think it is a Roman dish...")])
    client, _ = make_client(llm)
    _, body = ask(client, "How do I make carbonara?")
    resp = AskResponse.model_validate(body)
    assert resp.refused and resp.refusal_reason is RefusalReason.OUT_OF_CORPUS
    assert resp.answer == REFUSAL_TEMPLATES["out_of_corpus"] and resp.citations == []
    assert resp.meta.model == "fake-llm"  # the model was called; I-08 still holds


def test_empty_answer_is_refusal() -> None:
    """E-22."""
    llm = FakeLLM([answered(["spaghetti-alla-carbonara"], "   ")])
    client, _ = make_client(llm)
    _, body = ask(client, "How do I make carbonara?")
    assert AskResponse.model_validate(body).refusal_reason is RefusalReason.OUT_OF_CORPUS


@pytest.mark.parametrize("status", [LLMStatus.OUT_OF_CORPUS, LLMStatus.OUT_OF_DOMAIN])
def test_model_refusals_use_templates(status: LLMStatus) -> None:
    """R-20, R-22, R-23: model refusal → template text, no citations."""
    llm = FakeLLM([refusal(status)])
    client, _ = make_client(llm)
    _, body = ask(client, "What is the capital of France?")
    resp = AskResponse.model_validate(body)
    assert resp.refused and resp.refusal_reason is not None
    assert resp.refusal_reason.value == status.value
    assert resp.answer == REFUSAL_TEMPLATES[status.value]
    assert resp.citations == [] and resp.meta.model == "fake-llm"


def test_model_safety_status_uses_server_branch() -> None:
    """R-34: the model's safety status triggers the same ingredient-list answer."""
    llm = FakeLLM([refusal(LLMStatus.SAFETY)])
    client, _ = make_client(llm)
    _, body = ask(client, "Does the carbonara have anything I should worry about?")
    resp = AskResponse.model_validate(body)
    assert resp.refusal_reason is RefusalReason.SAFETY
    assert resp.answer is not None and resp.answer.startswith(SAFETY_DISCLAIMER)
    assert resp.citations and resp.meta.model == "fake-llm"


def test_llm_failure_is_503() -> None:
    """R-61: an operational failure is a 503 with a machine-readable error, never a refusal."""
    llm = FakeLLM([LLMUnavailableError("timeout")])
    client, _ = make_client(llm)
    r = client.post("/ask", json={"question": "How do I make carbonara?"})
    assert r.status_code == 503
    body = r.json()
    assert body["error"]["code"] == "llm_unavailable"
    assert body["error"]["request_id"] == r.headers["X-Request-ID"]
    assert "refused" not in body


def test_invalid_model_output_is_503() -> None:
    """R-41."""
    llm = FakeLLM([LLMInvalidOutputError("bad json")])
    client, _ = make_client(llm)
    r = client.post("/ask", json={"question": "How do I make carbonara?"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "llm_invalid_output"


def test_applied_constraints_echo_and_satisfied() -> None:
    """R-16, I-07: echoed constraints; every cited recipe satisfies them by metadata."""
    client, deps = make_client(FakeLLM())
    _, body = ask(client, "What's a vegetarian dinner I can make in under 30 minutes?")
    resp = AskResponse.model_validate(body)
    assert resp.applied_constraints.max_minutes == 30
    assert (
        resp.applied_constraints.diet is not None
        and resp.applied_constraints.diet.value == "vegetarian"
    )
    assert not resp.refused
    by_id = deps.corpus.by_id()
    for c in resp.citations:
        assert recipe_passes(by_id[c.recipe_id], resp.applied_constraints)


def test_bm25_fallback_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    """R-62: embedding failure at query time → bm25_only in meta."""
    client, _ = make_client(FakeLLM(), FakeEmbedder(fail=True))
    _, body = ask(client, "How do I make carbonara?")
    resp = AskResponse.model_validate(body)
    assert resp.meta.retrieval_mode.value == "bm25_only" and not resp.refused


def test_request_log_line(capsys: pytest.CaptureFixture[str]) -> None:
    """R-70: exactly one JSON line per request with the declared fields."""
    client, _ = make_client(FakeLLM())
    capsys.readouterr()
    _, body = ask(client, "quick vegetarian pasta")
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert len(lines) == 1
    rec = lines[0]
    for key in (
        "ts",
        "level",
        "request_id",
        "http_status",
        "question",
        "question_chars",
        "constraints",
        "safety_pattern",
        "retrieval_mode",
        "retrieved",
        "filtered_out",
        "llm",
        "outcome",
        "latency_ms",
    ):
        assert key in rec, key
    assert rec["request_id"] == body["meta"]["request_id"]
    assert rec["question"] == "quick vegetarian pasta"
    assert rec["constraints"]["max_minutes"] == 30
    assert {"id", "bm25_rank", "vec_rank", "bm25_score", "cosine", "rrf"} <= set(
        rec["retrieved"][0]
    )
    assert rec["llm"]["model"] == "fake-llm" and rec["llm"]["prompt_version"] == PROMPT_VERSION
    assert rec["outcome"]["cited_ids"] == [c["recipe_id"] for c in body["citations"]]
    assert set(rec["latency_ms"]) >= {"parse", "embed", "retrieve", "llm", "total"}


def test_unexpected_error_is_500_with_request_id() -> None:
    """R-64."""
    llm = FakeLLM([RuntimeError("boom")])
    client, _ = make_client(llm)
    r = client.post("/ask", json={"question": "How do I make carbonara?"})
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal"
    assert r.json()["error"]["request_id"] == r.headers["X-Request-ID"]
