"""Write the OpenAPI document derived from `schemas.py` (SPEC 3.1).

The frontend types are generated from this file by `openapi-typescript`; CI regenerates
them and fails on drift. Run: `uv run python scripts/export_openapi.py frontend/openapi.json`
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from recipe_qa.app import create_app


def openapi_document() -> dict[str, object]:
    app: FastAPI = create_app()
    doc: dict[str, object] = get_openapi(
        title=app.title, version=app.version, description=app.description, routes=app.routes
    )
    return doc


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else None
    text = json.dumps(openapi_document(), indent=2, sort_keys=True) + "\n"
    if out is None:
        sys.stdout.write(text)
    else:
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
