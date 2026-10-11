"""FastAPI application: routes, request ids and error handling (SPEC section 3).

Walking skeleton: `/health` and a `/ask` that returns a contract-valid refusal. The
pipeline stages (safety gate, constraints, retrieval, generation) are wired in later
steps; every response already satisfies the invariants in SPEC 3.5.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from recipe_qa.config import REFUSAL_TEMPLATES, STATIC_DIR, Settings, get_settings
from recipe_qa.schemas import (
    AppliedConstraints,
    AskRequest,
    AskResponse,
    ErrorBody,
    ErrorResponse,
    HealthResponse,
    Meta,
    RefusalReason,
    RetrievalMode,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

PLACEHOLDER_HTML = (
    "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
    "<title>Recipe Q&amp;A</title></head><body>"
    "<h1>Recipe Q&amp;A</h1><p>The UI has not been built into this image. "
    "The API is available: <code>POST /ask</code>, <code>GET /health</code>, "
    "<a href='/docs'>/docs</a>.</p></body></html>"
)


def _request_id(request: Request) -> str:
    rid: str | None = getattr(request.state, "request_id", None)
    return rid or "unknown"


def _error_response(status: int, code: str, message: str, request_id: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, request_id=request_id))
    return JSONResponse(
        status_code=status,
        content=body.model_dump(),
        headers={"X-Request-ID": request_id},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. `settings` is injectable for tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings or get_settings()
        # Corpus and index loading, with the hash check of R-63, arrive at steps 5 to 7.
        app.state.corpus_version = "none"
        yield

    app = FastAPI(
        title="Recipe Q&A Service",
        version="0.1.0",
        description="Answers questions from a fixed Wikibooks Cookbook corpus. Contract: SPEC.md.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # R-64: the stack trace goes to the log (uvicorn prints it), never to the client.
        del exc
        return _error_response(500, "internal", "Unexpected error.", _request_id(request))

    @app.get("/health", response_model=HealthResponse, tags=["ops"])
    async def health(request: Request) -> HealthResponse:
        # R-72: served from memory, no external calls.
        return HealthResponse(
            status="ok",
            git_sha=request.app.state.settings.git_sha,
            corpus_version=request.app.state.corpus_version,
        )

    @app.post(
        "/ask",
        response_model=AskResponse,
        responses={
            422: {"description": "Validation error (SPEC 3.4)."},
            500: {"model": ErrorResponse, "description": "Unexpected error."},
            503: {"model": ErrorResponse, "description": "Generation model unavailable."},
        },
        tags=["qa"],
    )
    async def ask(body: AskRequest, request: Request) -> AskResponse:
        started = time.perf_counter()
        # Skeleton behavior: no pipeline yet, every question is refused as out_of_corpus.
        latency_ms = int((time.perf_counter() - started) * 1000)
        return AskResponse(
            answer=REFUSAL_TEMPLATES["out_of_corpus"],
            citations=[],
            refused=True,
            refusal_reason=RefusalReason.OUT_OF_CORPUS,
            applied_constraints=AppliedConstraints(),
            meta=Meta(
                request_id=_request_id(request),
                model=None,
                prompt_version=None,
                corpus_version=request.app.state.corpus_version,
                retrieval_mode=RetrievalMode.NONE,
                latency_ms=latency_ms,
                usage=Usage(),
            ),
        )

    static_dir = Path(STATIC_DIR)
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
    else:

        @app.get("/", include_in_schema=False)
        async def placeholder() -> HTMLResponse:
            return HTMLResponse(PLACEHOLDER_HTML)

    return app


app: FastAPI = create_app()
