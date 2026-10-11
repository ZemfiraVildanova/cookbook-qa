---
name: write-adr
description: Template and rules for Architecture Decision Records in docs/adr. Use for ADR-001 retrieval, ADR-002 refusal and safety, ADR-003 model selection — drafts at step 3, numbers at step 13, acceptance at step 14.
---

# write-adr

File: `docs/adr/00N-<slug>.md`. Status moves `Proposed → Accepted`. Never delete a rejected option; mark it.

```markdown
# ADR-00N: <decision in one line>

- Status: Proposed | Accepted
- Date: YYYY-MM-DD
- Related: SPEC sections / rules, other ADRs

## Context
What forces the decision: the assignment requirement, corpus size, time budget, grading criteria.

## Decision criteria
Ordered list. Each criterion is measurable or at least checkable (e.g. hit@4 on the golden lookups, p50 latency, $ per 1,000, lines of code to maintain, failure modes).

## Options considered
| Option | Criterion 1 | Criterion 2 | Cost / latency | Notes |
Numbers in this table come from `evals/reports/*.json`, `evals/retrieval_eval.py` output, or a vendor pricing page with its URL and the date checked. A number without a source is written as `TBD (report: <name>)` until measured.

## Decision
One paragraph. What is built, with the parameters that matter (e.g. TOP_K = 4, RRF_K = 60).

## Consequences
Positive, negative, and what becomes harder.

## This decision is wrong when…
Concrete, observable conditions: corpus > N recipes, hit@4 below X on the golden lookups, p95 above Y s, cost above Z per 1,000, a requirement for streaming or multi-language, etc.

## Evidence
Links to the reports and the commit SHAs they were produced at.
```

Rules:

- No price, latency or token count from memory. Vendor pages: cite URL and date. Measurements: cite the report file.
- Each ADR names at least two real alternatives with their trade-offs, not strawmen.
- The "wrong when" section is the most important part for the follow-up session; make it specific.
- Deployment rationale does not get an ADR; it lives in README under Deployment.
