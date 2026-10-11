---
name: tdd-step
description: Test-before-implementation commit pair for deterministic modules (ingest, constraints, retrieval, safety gate, API contract). Use whenever SPEC rules get their first implementation.
---

# tdd-step

Two commits per unit of deterministic behavior. Never squash them.

## Commit 1 — `test: <module> (xfail)`

1. Write the tests in `tests/test_<module>.py`. Each test (or the module-level marker)
   references the SPEC rule it verifies in a docstring: `"""R-12: max_minutes passes iff total_minutes is known and <= limit."""`.
2. Mark every new test that cannot pass yet:
   ```python
   pytestmark = pytest.mark.xfail(strict=True, reason="implemented in next commit")
   ```
   or per test with `@pytest.mark.xfail(strict=True, reason="implemented in next commit")`.
   `strict=True` means an unexpected pass fails the suite, so the marker cannot be forgotten.
3. Imports of the not-yet-existing module go inside the test function or behind the marker,
   so collection does not error. If the module must exist for import, add the file with
   only signatures that `raise NotImplementedError`.
4. `uv run pytest -q` is green (xfails are reported as `x`). Commit and push.

## Commit 2 — `feat: <module>`

1. Implement in `src/recipe_qa/<module>.py`. Every constant comes from `config.py` and
   is in SPEC section 8.
2. Remove the `xfail` markers added in commit 1. Do not loosen a test to make it pass;
   if the test was wrong, fix it and say why in `AI_NOTES.md`.
3. `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`,
   `uv run mypy src` all clean. Commit and push.

## Rules

- Unit tests never use the network or a model. Use `FakeEmbedder` and `FakeLLM` from `tests/fakes.py`.
- Table-driven tests for parsers: `@pytest.mark.parametrize` with the phrasing and the expected structure.
- Fixtures for ingest are real wikitext pages stored under `tests/fixtures/`.
- Timebox per module is in the brief; if the implementation runs over, commit what passes and record the cut.
