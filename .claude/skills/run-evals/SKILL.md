---
name: run-evals
description: How to run the eval harness locally and against production, read the JSON report, and turn a bad answer into a new golden case. Use at steps 11–15 and whenever a prompt or pipeline change is made.
---

# run-evals

## Run

- Local service: `uv run uvicorn recipe_qa.app:app --port 8000` (needs `.env` with the keys; never print it), then
  `uv run python evals/run_evals.py --target http://localhost:8000 --runs 1`.
- Local container: `docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t cookbook-qa . && docker run --rm -d -p 8000:8000 --env-file .env --name cq cookbook-qa`, run the harness, `docker rm -f cq`.
- Production: `uv run python evals/run_evals.py --target https://<public-url> --runs 3`.
- Retrieval only (no model): `uv run python evals/retrieval_eval.py`.

The harness exits non-zero on any failure. Reports go to `evals/reports/<timestamp>-<target>.json`; commit the ones that back a number in README or an ADR.

## Read the report

For each case: `id`, `runs[]` with `passed`, `failures[]` (schema, invariant or assertion name), `latency_ms`, `input_tokens`, `output_tokens`, `cost_usd`, `refused`, `refusal_reason`, `cited_ids`. Summary: pass count, flaky count (passed in some runs only), p50/p95 latency for model answers and for pre-LLM exits, cost per question and per 1,000.

Pass rule (SPEC 12.3): a case passes only if every run passes.

## Fix a failure

1. Find the request in the service log by `request_id` (the harness prints it). Read `retrieved` with scores, `constraints`, `safety_pattern`, `llm.status`, `cited_ids`.
2. Classify: retrieval miss (expected id not in `retrieved`) → ADR-001 territory; filter (expected id filtered out) → metadata or parser; model (retrieved but wrong status or citations) → prompt.
3. A prompt change is a new file `prompts/answer.v2.md` and `PROMPT_VERSION` in config and SPEC section 8. A parser or metadata change goes through `spec-change` and `tdd-step`.
4. Each fix is its own `fix:` commit with one line in `AI_NOTES.md` saying what the failure was and what changed.

## Turn a bad answer into a golden case

Add to `evals/golden.yaml`: `id`, `group`, `question`, `expect` (assertion vocabulary in SPEC 12.3), and a `why` comment. Keep 15 cases: replace the weakest case of the same group and record the swap in `AI_NOTES.md`.
