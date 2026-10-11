"""API contract tests (SPEC section 3). No network, no model."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recipe_qa.app import create_app
from recipe_qa.config import QUESTION_MAX_CHARS, Settings
from recipe_qa.schemas import AskResponse, RefusalReason, RetrievalMode


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(git_sha="testsha", _env_file=None)  # type: ignore[call-arg]
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        yield c


def test_health_fields(client: TestClient) -> None:
    """SPEC 3.6: /health returns status, git_sha and corpus_version."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status", "git_sha", "corpus_version"}
    assert body["status"] == "ok"
    assert body["git_sha"] == "testsha"
    assert r.headers["X-Request-ID"]


def test_ask_safety_refusal_is_contract_valid(client: TestClient) -> None:
    """SPEC 3.3/3.5: a pre-model refusal validates against the schema and its invariants."""
    r = client.post("/ask", json={"question": "Is this nut-free?"})
    assert r.status_code == 200
    body = r.json()
    parsed = AskResponse.model_validate(body)  # enforces I-01..I-05, I-08
    assert set(body) == {
        "answer",
        "citations",
        "refused",
        "refusal_reason",
        "applied_constraints",
        "meta",
    }
    assert parsed.refused is True
    assert parsed.refusal_reason in set(RefusalReason)
    assert parsed.meta.retrieval_mode in set(RetrievalMode)
    assert r.headers["X-Request-ID"] == parsed.meta.request_id


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"question": ""}, id="empty"),
        pytest.param({"question": "   \n\t "}, id="whitespace"),
        pytest.param({"question": "x" * (QUESTION_MAX_CHARS + 1)}, id="too-long"),
        pytest.param({"question": "ok", "extra": 1}, id="extra-field"),
        pytest.param({}, id="missing"),
        pytest.param({"question": 5}, id="wrong-type"),
    ],
)
def test_validation_errors_are_422(client: TestClient, body: dict[str, object]) -> None:
    """R-60 / E-01..E-03: invalid requests are rejected with 422."""
    r = client.post("/ask", json=body)
    assert r.status_code == 422
    assert "detail" in r.json()


def test_malformed_json_is_422(client: TestClient) -> None:
    """P-03: a body that is not JSON is a 422."""
    r = client.post("/ask", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_question_at_max_length_is_accepted(client: TestClient) -> None:
    """SPEC 3.2: exactly QUESTION_MAX_CHARS after strip is valid."""
    prefix = "Is this nut-free? "
    question = prefix + "q" * (QUESTION_MAX_CHARS - len(prefix))
    r = client.post("/ask", json={"question": "  " + question + "  "})
    assert r.status_code == 200


def test_unknown_route_is_404(client: TestClient) -> None:
    """SPEC 3.1: no other routes exist."""
    assert client.get("/nope").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/ask").status_code == 405


def test_root_serves_html(client: TestClient) -> None:
    """SPEC 3.1: GET / is the UI (placeholder until the frontend is built)."""
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


@pytest.mark.xfail(strict=True, reason="implemented in next commit")
def test_built_ui_keeps_api_routes(tmp_path: Path) -> None:
    """SPEC 3.1: with a built UI, / is index.html and files live under /assets/; GET /ask stays
    405 and unknown paths 404, so the UI never shadows the API."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>ui</title>", encoding="utf-8")
    (tmp_path / "assets" / "app.css").write_text("body{}", encoding="utf-8")
    settings = Settings(git_sha="testsha", _env_file=None)  # type: ignore[call-arg]
    app = create_app(settings, static_dir=tmp_path)
    with TestClient(app, raise_server_exceptions=False) as c:
        root = c.get("/")
        assert root.status_code == 200 and "ui" in root.text
        assert root.headers["content-type"].startswith("text/html")
        assert c.get("/assets/app.css").status_code == 200
        assert c.get("/ask").status_code == 405
        assert c.get("/nope").status_code == 404


def test_response_schema_matches_spec() -> None:
    """SPEC 3.3: field names, enums and nullability of the JSON Schema."""
    schema = AskResponse.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "answer",
        "citations",
        "refused",
        "refusal_reason",
        "applied_constraints",
        "meta",
    }
    defs = schema["$defs"]
    assert set(defs["RefusalReason"]["enum"]) == {"out_of_corpus", "out_of_domain", "safety"}
    assert set(defs["RetrievalMode"]["enum"]) == {"hybrid", "bm25_only", "none"}
    assert set(defs["Citation"]["required"]) == {"recipe_id", "title", "url"}
    assert set(defs["Meta"]["required"]) == {
        "request_id",
        "model",
        "prompt_version",
        "corpus_version",
        "retrieval_mode",
        "latency_ms",
        "usage",
    }
    answer_types = {t["type"] for t in schema["properties"]["answer"]["anyOf"]}
    assert answer_types == {"string", "null"}
    # The schema is JSON-serialisable (it is what OpenAPI and the harness consume).
    json.dumps(schema)


def test_openapi_exposes_contract(client: TestClient) -> None:
    """SPEC 3.1: /openapi.json derives from schemas.py."""
    spec = client.get("/openapi.json").json()
    assert "/ask" in spec["paths"] and "/health" in spec["paths"]
    assert "AskResponse" in spec["components"]["schemas"]
