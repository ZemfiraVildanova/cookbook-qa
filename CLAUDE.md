# CLAUDE.md — Recipe Q&A Service

## Source of truth, in order
1. `SPEC.md`: contract, behavior rules `R-xx`, constants (section 8), traceability matrix.
2. `docs/adr/*.md`: accepted decisions.
3. `docs/ai/agent-brief.md`: step plan and verified deployment facts. SPEC wins on conflict.

Re-read `SPEC.md` and this file at the start of every step. Everything in the repository is English.

## Commands
- Install: `uv sync --frozen` · Tests: `uv run pytest -q` · Lint: `uv run ruff check . && uv run ruff format --check .`
- Types: `uv run mypy src` · Frontend: `cd frontend && npm ci && npm run typecheck && npm run build`
- Image: `docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t cookbook-qa .`
- Run: `docker run --rm -p 8000:8000 --env-file .env cookbook-qa`
- Evals: `uv run python evals/run_evals.py --target http://localhost:8000 --runs 1`
- Corpus: `uv run python scripts/ingest.py` (pinned by `data/corpus.lock.json`), then `uv run python scripts/build_index.py`

## Hard rules
- No behavior without a SPEC rule. No constant outside `src/recipe_qa/config.py` and SPEC section 8.
- No new dependency, endpoint or feature without a SPEC change in the same commit.
- Deterministic modules are test-first: the test commit marks new tests
  `@pytest.mark.xfail(strict=True, reason="implemented in next commit")`; the implementation commit removes the marker. `main` is green on every commit.
- Unit tests never touch the network or a model: use the fake embedder and the fake LLM.
- The response schema is defined once, in `src/recipe_qa/schemas.py`; OpenAPI, LLM output schema, evals and frontend types derive from it.
- Prices, latency and token counts come from vendor pages (with the date) or harness reports, never from memory.
- Never read, print or log `.env` or any key. Never put a key on a command line.
- Real timestamps only: no amend or rebase of pushed history, no artificial waits between commits.
- After each step: one line in `AI_NOTES.md`; prompts that mattered go verbatim into `docs/ai/prompts.md`.
- A step that runs more than 10 minutes over its timebox is cut by the order in the brief (section 7); the cut is recorded in README and `AI_NOTES.md`.

## Commits
`<prefix>: <imperative summary>`, prefix in `docs | chore | test | feat | fix | data | eval | ci | build`.
One logical change per commit. Author only: no `Co-Authored-By`, no AI attribution lines
(enforced by `attribution` in `.claude/settings.json` and by `scripts/check_commit_msg.sh`). Push after every commit.

## Skills and agents
- `spec-change`: new or changed behavior → SPEC rule with id → eval case → failing test → code → matrix.
- `tdd-step`: the xfail-then-implement commit pair.
- `run-evals`: run the harness locally or against production, read the report, turn a bad answer into a golden case.
- `write-adr`: ADR template; numbers only from reports or vendor pages.
- `spec-auditor` (read-only subagent): finds behavior not described in SPEC. Run after steps 9 and 11 and at step 15.

## Stop and ask the author only when
- a payment, account, permission or credential action is needed;
- the fallback deployment path (build on Northflank from a `deploy` branch) is required;
- a decision in SPEC or the brief turns out wrong in a way that changes the design;
- total elapsed time passes 7:30 with required work still open.

Otherwise keep going. Report after each step in five lines or fewer. Pause only at the
checkpoints after steps 4, 9, 11 and 15.
