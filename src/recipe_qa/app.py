"""FastAPI application: routes, request ids, dependency wiring and error mapping (SPEC 3).

The pipeline (`pipeline.py`) produces every outcome; this module only validates input,
wires dependencies at startup and maps exceptions to the error contract of SPEC 3.4.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from recipe_qa.config import EMBEDDING_DIM, EMBEDDING_MODEL, STATIC_DIR, Settings, get_settings
from recipe_qa.corpus import load_corpus
from recipe_qa.llm import LLMClient, LLMError
from recipe_qa.pipeline import Deps, Pipeline
from recipe_qa.retrieval import Embedder, OpenAIEmbedder, load_index
from recipe_qa.schemas import AskRequest, AskResponse, ErrorBody, ErrorResponse, HealthResponse

log = logging.getLogger(__name__)

PLACEHOLDER_HTML = (
    "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
    "<title>Recipe Q&amp;A</title></head><body>"
    "<h1>Recipe Q&amp;A</h1><p>The UI has not been built into this image. "
    "The API is available: <code>POST /ask</code>, <code>GET /health</code>, "
    "<a href='/docs'>/docs</a>.</p></body></html>"
)


def build_deps(settings: Settings) -> Deps:
    """Production wiring: committed corpus and index (R-63), API embedder, provider model."""
    corpus = load_corpus(settings.corpus_path)
    index = load_index(
        settings.embeddings_path, corpus, model=EMBEDDING_MODEL, dimension=EMBEDDING_DIM
    )
    embedder: Embedder | None = None
    if settings.openai_api_key is not None:
        embedder = OpenAIEmbedder(settings.openai_api_key.get_secret_value())
    else:
        log.warning("OPENAI_API_KEY is not set: retrieval runs in bm25_only mode")
    llm: LLMClient | None = None  # provider adapters are wired at step 9
    return Deps(corpus=corpus, index=index, embedder=embedder, llm=llm)


def _request_id(request: Request) -> str:
    rid: str | None = getattr(request.state, "request_id", None)
    return rid or "unknown"


def _error_response(status: int, code: str, message: str, request_id: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, request_id=request_id))
    return JSONResponse(
        status_code=status, content=body.model_dump(), headers={"X-Request-ID": request_id}
    )


def create_app(settings: Settings | None = None, deps: Deps | None = None) -> FastAPI:
    """Build the application. `settings` and `deps` are injectable for tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings or get_settings()
        wired = deps or build_deps(app.state.settings)
        app.state.pipeline = Pipeline(wired)
        app.state.corpus_version = wired.corpus.corpus_version
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

    @app.exception_handler(LLMError)
    async def llm_error(request: Request, exc: LLMError) -> JSONResponse:
        # R-61: an operational failure is a 503, never a refusal.
        return _error_response(
            503, exc.code, "The generation model is unavailable; try again.", _request_id(request)
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # R-64: the stack trace goes to the server log, never to the client.
        log.exception("unhandled error for request %s", _request_id(request), exc_info=exc)
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
        pipeline: Pipeline = request.app.state.pipeline
        return pipeline.ask(body.question, _request_id(request))

    static_dir = Path(STATIC_DIR)
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
    else:

        @app.get("/", include_in_schema=False)
        async def placeholder() -> HTMLResponse:
            return HTMLResponse(PLACEHOLDER_HTML)

    return app


app: FastAPI = create_app()
