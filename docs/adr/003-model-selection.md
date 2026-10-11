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

Prices were re-checked on the vendor pricing pages on 2026-10-11 (`evals/prices.yaml`).
Measured columns come from three runs of the 15-case golden set each: A against
production (`20261011T115552Z-production-haiku.json`) and locally
(`20261011T115629Z-local-haiku.json`), B locally (`20261011T115631Z-local-gpt-6-luna.json`);
the two local runs went through the same network path within the same minutes. Latency is
client-side wall time of answers that called the model (n = 36 each), nearest-rank
percentiles. Both candidates run with hidden reasoning switched off (`LLM_THINKING`,
`LLM_REASONING_EFFORT`): with the Anthropic default on, the same prompts cost 809–811
output tokens and 4.6–4.9 s instead of 241–282 tokens and 2.7 s (SPEC section 8).

| Option | Price per 1M tokens (in / out) | Structured output | Pass rate (3 runs) | Flaky | p50 / p95 | Cost per 1,000 | Verdict |
|---|---|---|---|---|---|---|---|
| A. Anthropic `claude-haiku-5-5`, thinking off | $0.10 / $0.50 | yes (`output_config` JSON schema) | 15/15 production, 15/15 local | 0 | production 2.20 s / 2.82 s; local 2.22 s / 2.80 s | $0.37 | **Default (Accepted)** |
| B. OpenAI `gpt-6-luna`, reasoning effort none | $0.10 / $0.50 | yes (`text.format` strict JSON schema) | 15/15 local | 0 | local 1.89 s / 3.49 s | $0.21 | Wired behind `LLM_PROVIDER=openai`; loses on p95 |
| C. A larger model of either vendor | higher | yes | not measured | — | — | — | Only if A and B fail the pass rule; see "wrong when" |
| D. A self-hosted small model | $0 per token, instance cost instead | partial | not measured | — | — | — | Rejected for v1: no GPU budget, operational load, weaker structured output |

Pre-measurement planning estimate (from the brief, not a measurement): about 2,300
input and 250 output tokens per answered question, which at the prices above is about
$0.36 per 1,000 answered questions before embeddings. README carries measured values only.

Embeddings are fixed by ADR-001 (`text-embedding-3-small`, $0.02 per 1M tokens); they are
part of the per-question cost but not of this decision.

## Decision

`claude-haiku-5-5` stays the default, with thinking off, output capped at
`LLM_MAX_OUTPUT_TOKENS = 1000`, 15 s timeout, one retry, no sampling parameters. The
bake-off rule set before measurement was: higher pass rate wins, ties broken by p95, then
by cost. Both candidates pass 15/15 with no flaky case; on p95 A wins (2.80 s against
3.49 s locally), so A is kept. B is cheaper per question ($0.21 against $0.37 per 1,000,
mostly fewer input tokens for the same prompt) and faster at p50 (1.89 s against 2.22 s);
it remains one environment variable away (`LLM_PROVIDER=openai`, `LLM_MODEL=gpt-6-luna`)
and is the first thing to try if A's p95 drifts above the target.

## Consequences

- Positive: both candidates sit at the same list price, so the decision rests on measured quality and latency; switching is a configuration change.
- Negative: one SDK (Anthropic) plus a raw HTTP adapter for OpenAI; two keys to manage; the bake-off cost under $0.05 and about 15 minutes of wall time.
- The thin adapter means no vendor-specific features (caching, batching) are used in v1.

## This decision is wrong when…

- The chosen model fails the pass rule on any golden case across 3 runs after one prompt revision: move up to a stronger model of the same vendor and record the cost delta.
- Measured p95 exceeds 6 s: try the other candidate; if both exceed it, reduce prompt size (`PROMPT_RECIPE_MAX_CHARS`) before changing models.
- Measured cost per 1,000 exceeds $1: cut `TOP_K` or recipe truncation first; a cheaper model only if quality holds.
- Vendor prices change by more than 2× relative to each other (re-check on the README date).
- A requirement for streaming or for a 100K+ context appears: re-evaluate with the vendor's streaming and long-context pricing.

## Evidence

- `evals/reports/20261011T115552Z-production-haiku.json`, `20261011T115629Z-local-haiku.json`, `20261011T115631Z-local-gpt-6-luna.json` (step 13, three runs each).
- Vendor prices: `evals/prices.yaml` (claude.com/pricing and developers.openai.com/api/docs/pricing, checked 2026-10-11).
- Thinking measurement: SPEC section 8 row `LLM_THINKING`; AI_NOTES rows 11c, 11d.
