# ADR-003: Generation model selection

- Status: Proposed
- Date: 2026-10-11
- Related: SPEC sections 5.6, 8, 9; rules R-41, R-50, R-54, R-61; README Cost & Latency

## Context

One generation call per answered question with structured output (status enum, answer,
citation ids restricted to the retrieved set). The task is extraction and composition
over at most four short recipes, not open-ended reasoning. Targets: p50 ≤ 3 s, p95 ≤ 6 s,
≤ $1 per 1,000 questions (SPEC 9). The provider is selected by environment behind a thin
`LLMClient` interface so the choice can be reversed by configuration.

## Decision criteria

1. Golden-set pass rate over 3 runs and the flaky share (a case that passes in some runs only counts as failed).
2. p50 and p95 latency of answered questions, measured by the harness against production.
3. Cost per 1,000 questions from measured token counts and the vendor price table.
4. Native structured-output support (JSON schema with enums), so the citation enum is enforced at generation time rather than only validated after.
5. Behaviour on prompt injection (G-11) and on refusals (G-08, G-10).

## Options considered

Prices below are from the brief, checked on the vendor pricing pages on 2026-10-10;
they are re-checked and dated again when README is written. Measured columns are
filled from `evals/reports/*.json` at step 13.

| Option | Price per 1M tokens (in / out) | Structured output | Pass rate (3 runs) | Flaky | p50 / p95 | Cost per 1,000 | Verdict |
|---|---|---|---|---|---|---|---|
| A. Anthropic `claude-haiku-5-5` | $0.10 / $0.50 | yes (JSON schema output format) | TBD | TBD | TBD | TBD | **Provisional default** |
| B. OpenAI `gpt-6-luna` | $0.10 / $0.50 | yes (JSON schema) | TBD | TBD | TBD | TBD | Comparison at step 13 |
| C. A larger model of either vendor | higher | yes | not measured | — | — | — | Only if A and B fail the pass rule; see "wrong when" |
| D. A self-hosted small model | $0 per token, instance cost instead | partial | not measured | — | — | — | Rejected for v1: no GPU budget, operational load, weaker structured output |

Pre-measurement planning estimate (from the brief, not a measurement): about 2,300
input and 250 output tokens per answered question, which at the prices above is about
$0.36 per 1,000 answered questions before embeddings. README carries measured values only.

Embeddings are fixed by ADR-001 (`text-embedding-3-small`, $0.02 per 1M tokens); they are
part of the per-question cost but not of this decision.

## Decision (provisional)

Develop and deploy with `claude-haiku-5-5` at temperature 0, output capped at
`LLM_MAX_OUTPUT_TOKENS = 600`, 15 s timeout, one retry. Run the bake-off at step 13 with
`--runs 3` on both A and B against the same golden set; the model with the higher pass
rate wins, ties broken by p95 then by cost. The result replaces "Proposed" with
"Accepted" and fills the table.

## Consequences

- Positive: both candidates sit at the same list price, so the decision rests on measured quality and latency; switching is a configuration change.
- Negative: two SDKs in the image; two keys to manage; the bake-off costs a few cents and about 15 minutes.
- The thin adapter means no vendor-specific features (caching, batching) are used in v1.

## This decision is wrong when…

- The chosen model fails the pass rule on any golden case across 3 runs after one prompt revision: move up to a stronger model of the same vendor and record the cost delta.
- Measured p95 exceeds 6 s: try the other candidate; if both exceed it, reduce prompt size (`PROMPT_RECIPE_MAX_CHARS`) before changing models.
- Measured cost per 1,000 exceeds $1: cut `TOP_K` or recipe truncation first; a cheaper model only if quality holds.
- Vendor prices change by more than 2× relative to each other (re-check on the README date).
- A requirement for streaming or for a 100K+ context appears: re-evaluate with the vendor's streaming and long-context pricing.

## Evidence

- `evals/reports/bakeoff-<model>-<date>.json` — TBD (step 13).
- Vendor pricing pages, re-checked and dated in README — TBD (step 14).
