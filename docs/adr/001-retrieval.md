# ADR-001: Whole-recipe retrieval units with hybrid BM25 + embedding retrieval and metadata filters

- Status: Proposed
- Date: 2026-10-11
- Related: SPEC sections 2, 4, 5.2; rules R-11..R-19, R-62, R-63; ADR-002

## Context

The corpus is 45–55 short Wikibooks recipes. Questions mix dish names ("carbonara"),
paraphrases ("something creamy with pasta and eggs") and constraints (time, diet,
ingredient). The assignment requires answers grounded in retrieved recipes, citations per
recipe, and obeyed constraints. There is no database and no vector store by decision
(brief, section 4): everything is loaded into memory from committed files. Retrieval
quality decides both grounding and the share of questions that can be answered at all.

## Decision criteria

1. hit@4 on the golden lookup questions and the same-dish pairs (`evals/retrieval_eval.py`): the expected recipe must be in the retrieved set, otherwise the model cannot cite it.
2. Constraint correctness: a cited recipe must satisfy the parsed constraints by metadata (SPEC I-07). Must be enforceable without the model.
3. Latency added before the model call (query embedding, scoring) — target well under the 500 ms budget for pre-LLM exits (SPEC 9).
4. Cost per question for retrieval: embedding tokens × price.
5. Operational simplicity: no new state, deterministic, works when the embedding API is down.
6. Citation granularity: a citation is a recipe page with a URL; the unit should match what is cited.

## Options considered

### Retrieval unit

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| A. Whole recipe (one unit per page) | Matches the citation unit; constraints and metadata belong to the page; no reassembly; ~50 units | Long pages dilute lexical and vector scores; method text can exceed prompt budget (mitigated by `PROMPT_RECIPE_MAX_CHARS`) | **Provisional choice** |
| B. Sections (ingredients and method as separate units) | Sharper matches for ingredient questions | Citations and filters need re-aggregation to the page; a step without its ingredients misleads the model; doubles the index for no gain at 50 pages | Rejected |
| C. Fixed-size chunks with overlap | Standard for long documents | Recipes are short; chunk boundaries cut ingredient lists; same re-aggregation problem | Rejected |

### Retrieval method

| Option | Dish-name questions | Paraphrase questions | Added latency | Cost per query | Offline fallback | Verdict |
|---|---|---|---|---|---|---|
| D. BM25 only (`rank-bm25`, title boosted) | Strong (exact tokens) | Weak | ~0 ms | $0 | n/a | Fallback mode (R-62) |
| E. Embeddings only (`text-embedding-3-small`, cosine) | Medium (names are often rare tokens) | Strong | one API call, TBD ms (harness `latency_ms.embed`) | ~TBD tokens × $0.02 / 1M | none | Rejected alone |
| F. Hybrid: D + E fused with reciprocal rank fusion (`RRF_K = 60`), then hard metadata filters, `TOP_K = 4` | Strong | Strong | same as E | same as E | degrades to D | **Provisional choice** |

Measured numbers to fill at step 7 and 13 (`evals/reports/retrieval-*.json`):

| Mode | hit@1 | hit@4 | MRR | Notes |
|---|---|---|---|---|
| BM25 only | TBD | TBD | TBD | |
| Embeddings only | TBD | TBD | TBD | |
| Hybrid RRF | TBD | TBD | TBD | |

### Constraint handling

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| G. Hard metadata filters after fusion, three-valued (unknown fails) | Verifiable without the model; $0; testable | Lower recall when metadata is unknown; depends on ingest quality | **Provisional choice** |
| H. Ask the model to obey constraints from the recipe text | No metadata needed | Not verifiable; model may miss a time or a hidden meat ingredient; violates "obey constraints" as a hard requirement | Rejected as the only mechanism; kept as best effort for unparsed constraints (R-18) |
| I. Filter before retrieval (pre-filter) | Smaller candidate set | Identical result at 50 recipes; more code paths | Rejected |

## Decision (provisional)

Index each recipe page as one unit. Score all recipes with BM25 (title repeated
`BM25_TITLE_BOOST` times) and with cosine similarity over `text-embedding-3-small`
vectors built offline by `scripts/build_index.py` and committed with the corpus hash.
Fuse the two rankings with RRF (`RRF_K = 60`), apply three-valued hard filters from the
parsed constraints, return the top `TOP_K = 4`. If the query embedding fails, use BM25
alone and report `retrieval_mode = bm25_only`. No relevance floor unless a tuning set
shows a clear margin (R-21).

## Consequences

- Positive: one in-memory structure, deterministic tests with a fake embedder, citations map one-to-one to retrieved units, constraints are enforced before the model sees anything.
- Negative: recall on constraint questions depends on ingest extracting time and diet correctly; a recipe without a declared time can never satisfy a time constraint (SPEC A-04).
- Harder later: a corpus of thousands of pages would need a real index and chunking; see "wrong when".

## This decision is wrong when…

- hit@4 for hybrid on the golden lookups is below 0.9 (then re-examine tokenisation, title boost or the unit).
- The corpus grows beyond roughly 500 pages or pages become long articles: whole-page units dilute scores and the prompt budget breaks; move to section units with page-level aggregation.
- Questions start to target a step inside a recipe ("at what temperature do I bake X?") more than a dish: section units become necessary.
- Query embedding latency pushes p50 for model answers above the 3 s target: embed asynchronously or switch to a local embedding model.
- More than one in ten golden constraint questions is refused only because metadata is unknown: invest in ingest, not in retrieval.

## Evidence

- `evals/reports/retrieval-<date>.json` (step 7, step 13) — TBD.
- Harness latency split `latency_ms.embed` and `latency_ms.retrieve` (step 11, 13) — TBD.
