# Recipe Q&A Service

Answers natural-language questions about recipes from a fixed selection of the Wikibooks
Cookbook, cites the recipe pages it used, obeys time, diet and ingredient constraints with
deterministic filters, and refuses politely and machine-readably when the corpus cannot
answer. One stateless container serves the API and a small UI.

- Production: **https://p01--web--mm97qxgjjcql.code.run** (UI at `/`, API at `/ask`, `/health`, OpenAPI at `/docs`)
- Contract and behavior rules: [`SPEC.md`](SPEC.md) (source of truth)
- Decisions: [ADR-001 retrieval](docs/adr/001-retrieval.md), [ADR-002 refusal and safety](docs/adr/002-refusal-and-safety.md), [ADR-003 model selection](docs/adr/003-model-selection.md)
- How the coding agent was used: [`AI_NOTES.md`](AI_NOTES.md), [`docs/ai/`](docs/ai/)

```bash
curl -sS -X POST https://p01--web--mm97qxgjjcql.code.run/ask \
  -H 'content-type: application/json' \
  -d '{"question": "What is a vegetarian dinner I can make in under 30 minutes?"}' | jq .
```

```json
{
  "answer": "Garlic Parmesan Pasta is vegetarian with a stated total time of 20 minutes ...",
  "citations": [
    {"recipe_id": "garlic-parmesan-pasta", "title": "Garlic Parmesan Pasta",
     "url": "https://en.wikibooks.org/wiki/Cookbook:Garlic_Parmesan_Pasta"}
  ],
  "refused": false,
  "refusal_reason": null,
  "applied_constraints": {"max_minutes": 30, "diet": "vegetarian",
                          "include_ingredients": [], "exclude_ingredients": []},
  "meta": {"request_id": "…", "model": "claude-haiku-5-5", "prompt_version": "answer.v3",
           "corpus_version": "40eeca491bc2", "retrieval_mode": "hybrid",
           "latency_ms": 2455, "usage": {"input_tokens": 3175, "output_tokens": 231}}
}
```

Refusals keep the same shape with `refused: true` and `refusal_reason` in
`out_of_corpus | out_of_domain | safety`; model failures are HTTP 503 with
`{"error": {"code", "message", "request_id"}}`, never disguised as refusals.

## Architecture

```
POST /ask ─▶ validate (1–500 chars) ─▶ constraint parser (time → diet → ingredients)
         ─▶ safety gate (regex, no model) ─▶ hybrid retrieval: BM25 (title ×3) + cosine
            over text-embedding-3-small, RRF fusion, three-valued metadata filters, top 4
         ─▶ pre-model exits ($0): safety → ingredient lists + disclaimer; empty set → out_of_corpus
         ─▶ one generation call, JSON-schema output {status, answer, cited_ids ∈ retrieved ids}
         ─▶ server guards (foreign ids dropped, no citation → out_of_corpus, templates for refusals)
         ─▶ one JSON log line per request ─▶ 200 with meta
```

- Corpus: 55 recipes (`data/corpus.json`, version `40eeca491bc2`, pinned by
  `data/corpus.lock.json` revision ids) and their embeddings (`data/embeddings.npz`),
  both committed and loaded into memory at start. The service refuses to start if the
  index does not match the corpus hash, model or dimension.
- Everything configurable lives in `src/recipe_qa/config.py` and SPEC section 8; the
  response schema is defined once in `src/recipe_qa/schemas.py` and the OpenAPI
  document, the model's output schema, the eval harness and the UI types derive from it.
- Frontend: Vite + TypeScript, one page, four states (loading, answer with citations,
  refusal with reason, error), served by the same process.

## Run locally

```bash
cp .env.example .env            # ANTHROPIC_API_KEY, OPENAI_API_KEY (embeddings)
docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t cookbook-qa .
docker run --rm -p 8000:8000 --env-file .env cookbook-qa
curl -sS localhost:8000/health
```

Without a Docker image: `uv sync --frozen && uv run uvicorn recipe_qa.app:app --port 8000`
(the UI needs `cd frontend && npm ci && npm run build` first; without it `/` serves a
placeholder page). Without `OPENAI_API_KEY` retrieval runs on BM25 alone
(`retrieval_mode = bm25_only`); without `ANTHROPIC_API_KEY` every question that reaches
the model is a 503.

## Rebuild the corpus

```bash
uv run python scripts/ingest.py --list "Soup recipes"        # inspect a category
uv run python scripts/ingest.py                               # pages and revisions from data/manifest.yaml + corpus.lock.json
uv run python scripts/ingest.py --refresh                     # move the lock to the latest revisions
uv run python scripts/build_index.py                          # embeddings for the new corpus hash
```

The manifest holds the selected pages and the reasons; the lock pins revision ids so the
corpus is reproducible. Changing the corpus changes `corpus_version`, and the index must
be rebuilt in the same commit or the service will not start.

## Tests and evals

| Layer | Command | What it proves |
|---|---|---|
| Unit (186 tests, no network, fake embedder and fake model) | `uv run pytest` | parser, filters, retrieval, safety gate, every `/ask` branch, contract and invariants |
| Lint and types | `uv run ruff check . && uv run ruff format --check . && uv run mypy src` | |
| Retrieval quality | `uv run python evals/retrieval_eval.py` | hit@1, hit@4, MRR per mode on 45 queries (ADR-001) |
| Golden set, 15 cases + 3 probes | `uv run python evals/run_evals.py --target http://localhost:8000 --runs 1` | schema and invariants on every response, then the case assertions; JSON report in `evals/reports/` |
| Production | `uv run python evals/run_evals.py --target https://p01--web--mm97qxgjjcql.code.run --runs 3` | the numbers below |

A case passes only if it passes in every run. CI runs the golden set against the freshly
built container before the image is pushed; the report is a workflow artifact.

## Cost & Latency

Measured against production on 2026-10-11 with three runs of the golden set
(`evals/reports/20261011T115552Z-production-haiku.json`; 36 model answers, 9 pre-model
refusals, 9 probes). Prices from the vendor pages on the same day
(`evals/prices.yaml`).

| Metric | Target (SPEC 9) | Measured | |
|---|---|---|---|
| Answers that call the model, p50 / p95 | ≤ 3.0 s / ≤ 6.0 s | **2.20 s / 2.82 s** | met |
| Pre-model refusals (safety, filtered out) | ≤ 500 ms | 242–249 ms | met |
| Cost per 1,000 questions, golden mix | ≤ $1.00 | **$0.37** ($0.00037 per question) | met |
| Correctness | 15/15 on 3 consecutive runs, 3/3 probes | 15/15, 0 flaky, 3/3 | met |

What one question costs: about 3,400 input tokens (system prompt plus four recipes) and
200–350 output tokens on `claude-haiku-5-5` at $0.10 / $0.50 per million, plus about 16
embedding tokens at $0.02 per million (below a thousandth of a cent). Refusals decided
before the model cost nothing.

Models and why: generation on **`claude-haiku-5-5`** with extended thinking switched off
(`LLM_THINKING = disabled`): the model's default emits a hidden thinking block that
tripled output tokens and nearly doubled latency on the same prompts (SPEC section 8,
measured 2026-10-11). The alternative `gpt-6-luna` (same list price) is wired behind
`LLM_PROVIDER=openai` and was measured the same way, three local runs: 15/15, p50 1.89 s,
p95 3.49 s, $0.21 per 1,000, against 2.22 s / 2.80 s / $0.37 for Haiku on the same
machine; the pre-registered tie-break (p95) keeps Haiku (ADR-003). Embeddings on
`text-embedding-3-small`: on the harder 45-query set embeddings alone reach hit@1 1.00
where BM25 reaches 0.96; the hybrid is kept for its BM25 fallback (ADR-001).

Move to a cheaper or stronger model when (ADR-003): a golden case fails the pass rule on
three runs after one prompt revision (stronger); p95 exceeds 6 s or cost exceeds $1 per
1,000 after prompt-size cuts (other candidate); the vendors' relative prices change by
more than 2×.

Current bottleneck: the generation call itself, about 2 s of the 2.2 s median; retrieval
and the embedding call take under 250 ms together. Next optimization, in order: prompt
caching of the fixed system prompt, a smaller recipe rendering (`PROMPT_RECIPE_MAX_CHARS`),
and streaming the answer to the UI, none of which changes the contract.

## Deployment

- **Where**: Northflank, team `zemfira-cookbook-qa`, project `cookbook-qa`, service
  `web` (plan `nf-compute-20`, region `europe-west`), one instance, image
  `ghcr.io/zemfiravildanova/cookbook-qa:<git sha>` pulled with registry credentials.
- **Why Northflank**: a declarative template (`northflank-template.json`) plus an API brings
  the project, the secret group and the service up from the repository with no UI steps;
  role-based access gives a reviewer the containers, logs and metrics of one project
  without its secrets; per-second billing at about $5 a month for this plan; EU region.
  Revisit when private networking, managed databases or a company AWS standard appear;
  then Terraform.
- **How a deployment runs** (`.github/workflows/ci.yml`): lint, types, tests, secrets
  scan → frontend type check and build with an API-types drift check → image build →
  the full golden set against the built container (eval gate) → push to GHCR →
  `scripts/bootstrap.sh` creates or updates the Northflank template and runs it with the
  image tag and the keys as template arguments → waits for the expected `git_sha` in
  `/health` → `scripts/smoke.sh` runs three production cases (answer with citation,
  `out_of_domain`, `safety` without a model call). The same smoke runs daily at 06:17
  UTC and on demand (`smoke_only` dispatch input). Deploys are serialised by a
  concurrency group; re-running the workflow for the same commit reapplies the same
  template with the same image and changes nothing. Rollback: dispatch the workflow with
  a previous `image_tag`, or revert the commit.
- **Secrets**: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `NORTHFLANK_API_TOKEN` live in
  GitHub Actions secrets; the first two are written into a Northflank secret group by the
  template run and reach the container as environment variables. No key is in the
  repository, the image or a command line; `gitleaks` runs on every push.
- **Container-level access for reviewers**: a Northflank team invitation with a read-only
  role scoped to the `cookbook-qa` project (services, containers, logs, metrics, no
  secrets). The author sends the invitation to the reviewer's e-mail on request; the
  same logs are the JSON lines described in the next section.

## When a bad answer occurs in production

Every `/ask` request writes one JSON line to the container log (Northflank → service
`web` → Logs; filter by the `request_id` from the response `meta` or the `X-Request-ID`
header). The line holds the question, the parsed `constraints`, the `safety_pattern`
that fired, `retrieval_mode`, the `retrieved` set with BM25 rank, vector rank, scores and
RRF value, the first ten fused candidates before filters (`top_ranked`, for "why not X"
questions), `filtered_out`, the model, prompt version, attempts, tokens and status, the
outcome (refused, reason, cited ids) and per-stage latency. From that line the cause is
one of three: the expected recipe was not retrieved (retrieval, ADR-001), it was retrieved
but filtered (metadata or parser, SPEC 2.3 and 5.2), or it was shown to the model and the
model answered badly (prompt, SPEC 5.6). The fix path for each is in
`.claude/skills/run-evals/SKILL.md`; a bad answer becomes a golden case first.

## Production gaps

| Gap | What closes it |
|---|---|
| No authentication | an API key or OIDC in front of `/ask`; the UI would need a session |
| No per-IP rate limiting | a limiter in the app (`slowapi`) or at the edge; spend is bounded today by vendor spend limits, the output-token cap and the question-length cap |
| No streaming | server-sent events from the generation call; the contract gains a streamed variant |
| No conversation history | a session store and a prompt that carries the previous turn; out of scope by design (one question, one answer) |
| English only | per-language vocabularies for the parser (table-driven) and a language field in the prompt |
| Corpus updates need a redeploy | scheduled ingest + index build in CI with a review step; the hash check already guards consistency |
| No load test | a `k6` or `locust` scenario against a staging service; one instance today |
| No frontend tests | Playwright over the four states |
| No alerting | log-based alerts on `http_status >= 500` and `retrieval_mode = bm25_only` rates; the daily smoke is the only watchdog |

## Cut scope

- Relevance floor for `out_of_domain` (`SIMILARITY_FLOOR`) stays disabled: the model
  decides (R-21); enabling it needs a 20-question tuning set that shows a margin.
- The SPEC v1 quota of eight template-less pages was replaced by a measured quota: only
  two such pages exist in the categories used.
- UI polish, accessibility beyond semantic HTML, frontend tests, prompt caching.
- Nothing from the brief's cut list was cut: the second-model comparison, three eval
  runs and the retrieval evaluation are all in place.

## Time log

First commit 10:16, Europe/Moscow, 2026-10-11. Times are commit times from `git log`.

| Step | Done at | Notes |
|---|---|---|
| 1 SPEC | 10:16 | three review rounds with the author before the first commit |
| 2 agent files, 3 ADR drafts | 10:24 | |
| 4 skeleton, Dockerfile, CI, first deploy | 10:41 | two CI fixes (`setup-uv` tag, subshell status) |
| 5 corpus and ingest | 11:30 | 55 recipes; quota corrected in SPEC |
| 6 constraints and filters | 11:34 | 30 phrasings pass first time |
| 7 retrieval | 11:39 | eval set too easy, hardened at step 13 |
| 8 safety gate and pipeline | 11:46 | |
| 9 generation | 11:52 | checkpoint with the author until 13:09 |
| 10 frontend | 13:16 | prepared during the checkpoint |
| R-36 safety scope (author request) | 13:19 | |
| 11 golden set, harness, prompt v2, audit fixes | 13:40 | checkpoint; then prompt v3 and thinking off by 14:13 |
| 12 CI gate, smoke, schedule | 14:48 | |
| 13 OpenAI adapter, bake-off, retrieval numbers | 15:00 | three runs per model, 45-query retrieval set |
| 14 README, ADRs | | |
| 15 final verification | | |

## Licence

Recipe text is CC BY-SA 4.0 from the Wikibooks Cookbook; every answer cites the source
page URL, which is the attribution. The corpus is a pinned snapshot (`data/corpus.lock.json`)
and does not follow later edits on Wikibooks.
