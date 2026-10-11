"""Embed the committed corpus and write data/embeddings.npz (SPEC 2.4, ADR-001).

The file carries the embedding model name, the vector dimension and the corpus_version it
was built from; the service refuses to start when that version differs from corpus.json
(R-63). Needs OPENAI_API_KEY in the environment or in .env.

Run: `uv run python scripts/build_index.py`
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from recipe_qa.config import EMBEDDING_DIM, EMBEDDING_MODEL, get_settings
from recipe_qa.corpus import load_corpus
from recipe_qa.retrieval import OpenAIEmbedder, build_vectors, save_index

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "data" / "corpus.json")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "embeddings.npz")
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.openai_api_key is None:
        print("error: OPENAI_API_KEY is not set", file=sys.stderr)
        return 2
    corpus = load_corpus(args.corpus)
    embedder = OpenAIEmbedder(settings.openai_api_key.get_secret_value())
    vectors, ids = build_vectors(corpus, embedder)
    if vectors.shape != (len(ids), EMBEDDING_DIM):
        print(f"error: unexpected vector shape {vectors.shape}", file=sys.stderr)
        return 1
    save_index(
        args.out,
        vectors=vectors,
        ids=ids,
        model=EMBEDDING_MODEL,
        corpus_version=corpus.corpus_version,
    )
    print(
        f"wrote {len(ids)} vectors ({EMBEDDING_MODEL}, dim {EMBEDDING_DIM}) for corpus "
        f"{corpus.corpus_version} to {args.out.relative_to(ROOT)}; "
        f"embedding tokens used: {embedder.total_tokens}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
