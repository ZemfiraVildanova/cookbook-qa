---
name: spec-change
description: Procedure for any new or changed behavior — SPEC rule first, then eval case, then failing test, then code, then the traceability matrix. Use before adding a feature, a constant, a dependency, an endpoint or changing how an existing rule behaves.
---

# spec-change

Behavior changes land in this order, in this many commits:

1. **SPEC.md** (`docs:` commit, or the same commit as the code when the change is one line).
   - Add or edit a rule `R-xx` in section 5 with a one-sentence statement and a `**V:**` verification method.
   - If a constant or heuristic is involved, add or update its row in section 8 (name → value → env → purpose). The value in `config.py` must match.
   - If an edge case is involved, add a row to section 7.
   - If an assumption is involved, add a row to section 13.
   - Update the change log in section 15 when the change is made after v1 is committed.
2. **Eval case** (`eval:` commit) when the behavior involves the model or end-to-end flow: add a case to `evals/golden.yaml` with its expected properties, keep the total at 15 (replace a weaker case if needed and say so in `AI_NOTES.md`).
3. **Failing test** (`test:` commit) for deterministic logic, marked `xfail(strict=True, reason="implemented in next commit")`, docstring referencing the rule id. See `tdd-step`.
4. **Implementation** (`feat:` or `fix:` commit) that removes the marker. Constants go to `config.py` only.
5. **Traceability matrix** (section 14): the requirement row points at the test name or eval id. Done in the same commit as the implementation.

Checks before the final commit of the change:

- `uv run pytest -q` green, `uv run ruff check .` and `uv run mypy src` clean.
- `grep -n "<constant name>" SPEC.md src/recipe_qa/config.py` finds both.
- No new dependency unless SPEC names the library.
- One line in `AI_NOTES.md`: step → what changed → why.
