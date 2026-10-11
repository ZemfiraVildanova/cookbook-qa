"""Retrieval quality by mode: BM25 only, embeddings only, hybrid RRF (SPEC 12.2, ADR-001).

Reads the committed corpus and index, embeds each query once with the real embedder, and
reports hit@1, hit@4 and MRR per mode. No constraints are applied: this measures ranking.
Needs OPENAI_API_KEY for the embedding modes; without it only BM25 is reported.

Run: `uv run python evals/retrieval_eval.py`
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from recipe_qa.config import EMBEDDING_DIM, EMBEDDING_MODEL, TOP_K, get_settings
from recipe_qa.corpus import load_corpus
from recipe_qa.retrieval import OpenAIEmbedder, load_index
from recipe_qa.schemas import AppliedConstraints

ROOT = Path(__file__).resolve().parent.parent


def metrics(rankings: list[list[str]], expected: list[list[str]]) -> dict[str, float]:
    hit1 = hit4 = 0
    rr = 0.0
    for ranking, expect in zip(rankings, expected, strict=True):
        positions = [i for i, rid in enumerate(ranking, start=1) if rid in expect]
        if positions:
            first = positions[0]
            hit1 += first == 1
            hit4 += first <= TOP_K
            rr += 1.0 / first
    n = len(rankings)
    return {"hit@1": hit1 / n, f"hit@{TOP_K}": hit4 / n, "mrr": rr / n, "n": n}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=ROOT / "evals" / "retrieval_queries.yaml")
    parser.add_argument("--corpus", type=Path, default=ROOT / "data" / "corpus.json")
    parser.add_argument("--embeddings", type=Path, default=ROOT / "data" / "embeddings.npz")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "evals" / "reports")
    args = parser.parse_args(argv)

    corpus = load_corpus(args.corpus)
    index = load_index(args.embeddings, corpus, model=EMBEDDING_MODEL, dimension=EMBEDDING_DIM)
    queries = yaml.safe_load(args.queries.read_text(encoding="utf-8"))["queries"]
    known = set(corpus.by_id())
    for item in queries:
        unknown = [rid for rid in item["expect"] if rid not in known]
        if unknown:
            print(f"error: unknown ids in queries file: {unknown}", file=sys.stderr)
            return 2

    settings = get_settings()
    embedder = (
        OpenAIEmbedder(settings.openai_api_key.get_secret_value(), timeout=30.0)
        if settings.openai_api_key
        else None
    )
    if embedder is None:
        print("warning: OPENAI_API_KEY not set, reporting BM25 only", file=sys.stderr)

    texts = [q["q"] for q in queries]
    expected = [q["expect"] for q in queries]
    rankings: dict[str, list[list[str]]] = {"bm25": [], "vectors": [], "hybrid": []}
    per_query: list[dict[str, Any]] = []
    for text, expect in zip(texts, expected, strict=True):
        bm25 = [rid for rid, _ in index.bm25_ranking(text)]
        rankings["bm25"].append(bm25)
        row: dict[str, Any] = {"q": text, "expect": expect, "bm25_top": bm25[:TOP_K]}
        if embedder is not None:
            vec = [rid for rid, _ in index.vector_ranking(embedder.embed([text])[0])]
            hybrid = [c.id for c in index.retrieve(text, AppliedConstraints(), embedder).ranked]
            rankings["vectors"].append(vec)
            rankings["hybrid"].append(hybrid)
            row["vectors_top"] = vec[:TOP_K]
            row["hybrid_top"] = hybrid[:TOP_K]
        per_query.append(row)

    results = {mode: metrics(r, expected) for mode, r in rankings.items() if r}
    print(f"{'mode':10s} {'hit@1':>7s} {'hit@' + str(TOP_K):>7s} {'mrr':>7s}  (n={len(texts)})")
    for mode, m in results.items():
        print(f"{mode:10s} {m['hit@1']:7.2f} {m[f'hit@{TOP_K}']:7.2f} {m['mrr']:7.3f}")
    misses = [
        (row["q"], row["expect"], row.get("hybrid_top", row["bm25_top"]))
        for row in per_query
        if not set(row["expect"]) & set(row.get("hybrid_top", row["bm25_top"]))
    ]
    for q, expect, top in misses:
        print(f"  miss@{TOP_K}: {q!r} expected {expect} got {top}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "generated_at": stamp,
        "corpus_version": corpus.corpus_version,
        "embedding_model": EMBEDDING_MODEL if embedder else None,
        "embedding_tokens": embedder.total_tokens if embedder else 0,
        "top_k": TOP_K,
        "results": results,
        "per_query": per_query,
    }
    out = args.out_dir / f"retrieval-{stamp}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"report: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
