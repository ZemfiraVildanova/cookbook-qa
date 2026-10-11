# ADR-002: Machine-readable refusals with deterministic pre-model gates, and a no-verdict safety policy

- Status: Accepted (2026-10-11, after the step-13 measurements)
- Date: 2026-10-11
- Related: SPEC sections 4, 5.3, 5.4, 6, 7; rules R-19..R-23, R-30..R-35, R-40, R-41, R-61; ADR-001

## Context

The assignment requires refusing questions the corpus cannot answer, refusing politely,
making every refusal detectable "without analysis of natural language", and being
"careful" with allergy and safety questions while recording what careful means. A wrong
"this is nut-free" is the single most harmful output the service could produce. At the
same time the eval harness must be able to assert refusals and their reasons, and
refusals should be cheap.

## Decision criteria

1. Detectability: a reviewer or a script identifies a refusal and its reason from fields, never from text.
2. Harm bound: the service can never assert absence of an allergen or safety for a condition.
3. Testability without the model: the branches that matter most are deterministic and unit-tested.
4. Cost: refusals that can be decided before the model call cost $0 and return fast.
5. Honesty of errors: an operational failure is distinguishable from a refusal.
6. Usefulness: a refused user still gets something actionable (the ingredient lists, a hint to relax constraints).

## Options considered

### Refusal representation

| Option | Detectable | Testable | Verdict |
|---|---|---|---|
| A. Polite text only in `answer`, model decides | No | No | Rejected by the assignment |
| B. `refused` boolean + `refusal_reason` enum, text from fixed templates | Yes | Yes | **Chosen** |
| C. HTTP status codes for refusals (e.g. 404 out of corpus) | Yes | Yes | Rejected: conflates transport errors with content decisions; UI and harness need the body anyway |

### Who decides a refusal

| Reason | Option | Verdict |
|---|---|---|
| `out_of_corpus` | server when filters leave nothing (R-19) or the model cites nothing (R-04); model via `status` otherwise | **Chosen**: deterministic where possible, model where judgement is needed |
| `out_of_domain` | model via `status`; optional similarity floor only if a tuning set shows a margin | **Chosen**; floor disabled by default (R-21) |
| `safety` | server regex gate before the model (R-30), model `status = safety` as fallback (R-34) | **Chosen** |

### Safety policy ("careful")

| Option | Harm bound | Usefulness | Cost | Verdict |
|---|---|---|---|---|
| D. Answer with a disclaimer ("may contain nuts, check yourself") | Weak: the model still makes claims about contents | High | one call | Rejected |
| E. Refuse with a generic message, no content | Strong | Low | $0 | Rejected: unhelpful |
| F. Refuse (`safety`), show the retrieved recipes' ingredient lists verbatim plus a fixed disclaimer, cite them; never a verdict | Strong (template text only) | Medium: the user can check the lists | $0 | **Chosen** |
| G. Model-generated allergen analysis from ingredient lists | Weak: hidden ingredients (stock, sauces), cross-contamination | High | one call | Rejected |

Boundary decisions recorded in SPEC: "X-free" with an allergen is a safety question
(R-33); "without X" is an ordinary exclusion filter phrased as "does not list X";
the gate takes precedence over `out_of_corpus` (R-35).

### Errors

| Option | Verdict |
|---|---|
| H. Model failure → `refused = true` | Rejected: hides outages in refusal metrics |
| I. Model failure → HTTP 503 with a machine-readable error code | **Chosen** (R-61) |

## Decision

Refusals are `refused = true` with `refusal_reason` from a closed enum and a fixed
template in `answer`. Deterministic gates run before the model: validation (422), safety
patterns, empty candidate set. The model decides `out_of_domain` and the residual
`out_of_corpus` through a constrained `status` field; the server overrides the model
whenever a citation guard or the safety branch applies. Safety questions receive
ingredient lists verbatim and a disclaimer; the service never states that a dish is free
of anything.

Two refinements landed after the author reviewed production output (SPEC v1.8 and v1.11):
R-36 narrows the safety answer to the retrieved recipes whose title shares a word with
the question (all of them when none does), and R-24 reserves `out_of_corpus` for a named
dish, ingredient or property that the recipes lack, so open questions ("what can I cook
with my kids") are answered with recommendations.

## Consequences

- Positive: every refusal is testable; two of three refusal reasons have $0 paths; the harm bound holds by construction (template text); evals assert reasons directly.
- Negative: the safety gate is conservative — "gluten-free bread recipe" is refused with ingredient lists rather than answered; refusal text cannot mention near-miss recipes (template).
- Trade-off recorded: precision over recall for safety; helpfulness is recovered by showing ingredients.

## This decision is wrong when…

- The golden set shows `out_of_domain` flakiness across 3 runs (then a declared pattern list or the similarity floor replaces model judgement for the common cases).
- Users need allergen answers as a product feature: that requires curated allergen metadata per recipe and a reviewed policy, not a prompt.
- The share of safety refusals among real questions exceeds what the product accepts (measured from logs): narrow the patterns, through SPEC.
- A refusal needs to carry alternatives ("not in the corpus, but here are three pasta recipes"): switch the template to a model-written refusal with its own guardrails.

## Evidence

- $0 exits in the production golden runs (`evals/reports/20261011T115552Z-production-haiku.json`, 3 runs): 3 of 15 cases per run (impossible constraints, two safety questions) answered before the model in 202–258 ms wall time; the three 422 probes in 71–74 ms. `out_of_domain` and `out_of_corpus` decided by the model: 0 flaky cases in 3 runs.
- `tests/test_safety.py`, `tests/test_api_contract.py` — branch coverage listed in SPEC section 14.
