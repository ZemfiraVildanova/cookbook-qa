# AI usage notes

How the coding agent (Claude Code) was used, step by step. The instruction files it
worked from are committed: `CLAUDE.md`, `.claude/skills/*/SKILL.md`,
`.claude/agents/spec-auditor.md`, `.claude/settings.json`, `docs/ai/agent-brief.md`.
Prompts that shaped the result are in `docs/ai/prompts.md`.

Legend: **accepted** = used as produced; **rewritten** = the author changed the substance;
**cut** = dropped for time, recorded in README.

| Step | What the agent produced | Accepted / rewritten | Why |
|---|---|---|---|
| 1 | `SPEC.md` v1 from the brief: contract, 43 rules, edge cases, constants table, NFR targets, matrix. | Rewritten in three rounds before the first commit: ingredient candidates are accepted by the filter's own matching rule against corpus lines (not a token vocabulary); `INGREDIENT_GROUPS` (nut, dairy, meat, fish, seafood); veto terms match whole words with `VETO_EXCEPTIONS`; stopwords `one, way, time, clue, idea`. The agent added `INGREDIENT_MIN_CHARS = 3` and flagged it. "X-free with an allergen is a safety refusal" was questioned by the agent and kept by the author. | The agent's first draft of the ingredient parser accepted any word after "with"/"without", which would have turned "dinner with friends" into a filter and refused the question. |
| 2 | `CLAUDE.md`, four skills, `spec-auditor`, hook scripts under `.claude/hooks/`, `.pre-commit-config.yaml`, `scripts/check_commit_msg.sh`, this file, `docs/ai/prompts.md`. The agent drafted `.claude/settings.json` (attribution off, permissions, hooks) but Claude Code's auto-mode classifier refused to let the agent write its own settings file; the author created it from the agent's draft. | Accepted. | Mechanical; the `attribution` value was checked against the Claude Code settings reference rather than written from memory. |
| 3 | ADR drafts 001 (retrieval unit and method), 002 (refusal and safety policy), 003 (model selection), status Proposed, numbers marked TBD with the report they will come from. | Accepted as drafts; to be revisited at steps 13–14 with measurements. | Options and "wrong when" conditions follow the brief; no prices beyond the brief's dated table. |
