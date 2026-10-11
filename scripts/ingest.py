"""Build the recipe corpus from the Wikibooks Cookbook through the MediaWiki API (SPEC 2).

Modes:
  --list CATEGORY [CATEGORY ...]  print candidate pages of the categories with flags
  (default)                       build data/corpus.json from data/manifest.yaml; with a lock
                                  file present, fetch exactly the locked revisions
  --refresh                       ignore the lock, fetch the latest revisions, rewrite the lock

Only the manifest decides what is in the corpus; the lock pins revisions so the build is
reproducible. Run: `uv run python scripts/ingest.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from recipe_qa.corpus import Corpus, Recipe, compute_version
from recipe_qa.ingest import parse_page

API = "https://en.wikibooks.org/w/api.php"
USER_AGENT = "cookbook-qa-ingest/0.1 (take-home assignment; contact: vildanovazemfira@gmail.com)"
SOURCE = "English Wikibooks Cookbook (namespace 102) via the MediaWiki API"
LICENSE = "CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)"
BATCH = 50
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "data" / "manifest.yaml"
DEFAULT_LOCK = ROOT / "data" / "corpus.lock.json"
DEFAULT_OUT = ROOT / "data" / "corpus.json"


class Api:
    def __init__(self) -> None:
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True
        )

    def get(self, **params: Any) -> dict[str, Any]:
        params.update(format="json", formatversion="2")
        for attempt in range(3):
            try:
                r = self.client.get(API, params=params)
                r.raise_for_status()
                data: dict[str, Any] = r.json()
                if "error" in data:
                    raise RuntimeError(f"MediaWiki error: {data['error']}")
                return data
            except (httpx.HTTPError, RuntimeError):
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
        raise AssertionError("unreachable")

    def category_members(self, category: str) -> list[str]:
        titles: list[str] = []
        cont: dict[str, Any] = {}
        while True:
            data = self.get(
                action="query",
                list="categorymembers",
                cmtitle=f"Category:{category}",
                cmnamespace=102,
                cmlimit=500,
                **cont,
            )
            titles += [m["title"] for m in data["query"]["categorymembers"]]
            if "continue" not in data:
                return titles
            cont = data["continue"]

    def pages(
        self, *, titles: list[str] | None = None, revids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        """Pages with content, categories and canonical URL; redirects resolved for titles."""
        keys = titles if titles is not None else [str(r) for r in (revids or [])]
        out: list[dict[str, Any]] = []
        for i in range(0, len(keys), BATCH):
            batch = keys[i : i + BATCH]
            params: dict[str, Any] = dict(
                action="query",
                prop="revisions|categories|info",
                rvprop="content|ids",
                rvslots="main",
                cllimit="max",
                inprop="url",
            )
            if titles is not None:
                params.update(titles="|".join(batch), redirects=1)
            else:
                params.update(revids="|".join(batch))
            data = self.get(**params)
            for page in data["query"].get("pages", []):
                if "missing" in page or "revisions" not in page:
                    print(f"warning: page not found or empty: {page.get('title')}", file=sys.stderr)
                    continue
                rev = page["revisions"][0]
                content = rev["slots"]["main"]["content"]
                if content.lstrip().upper().startswith("#REDIRECT"):
                    print(f"warning: unresolved redirect skipped: {page['title']}", file=sys.stderr)
                    continue
                out.append(
                    {
                        "title": page["title"],
                        "page_id": page["pageid"],
                        "revision_id": rev["revid"],
                        "url": page["canonicalurl"],
                        "categories": [
                            c["title"].removeprefix("Category:") for c in page.get("categories", [])
                        ],
                        "wikitext": content,
                    }
                )
            time.sleep(0.2)
        return out


def to_recipe(page: dict[str, Any]) -> Recipe:
    return parse_page(
        title=page["title"],
        page_id=page["page_id"],
        revision_id=page["revision_id"],
        url=page["url"],
        categories=page["categories"],
        wikitext=page["wikitext"],
    )


def cmd_list(api: Api, categories: list[str]) -> int:
    seen: set[str] = set()
    print("title | template | minutes | vegetarian/vegan | ingredients | chars | categories")
    for category in categories:
        titles = [t for t in api.category_members(category) if t not in seen]
        seen.update(titles)
        for page in sorted(api.pages(titles=titles), key=lambda p: p["title"]):
            r = to_recipe(page)
            diet = f"{r.diet['vegetarian'][0]}/{r.diet['vegan'][0]}"
            cats = ", ".join(
                c for c in r.categories if c.endswith(" recipes") and not c.startswith("Recipes ")
            )[:60]
            template = "tpl" if r.has_summary_template else "no-tpl"
            minutes = r.total_minutes if r.total_minutes is not None else "-"
            print(
                f"{r.title} | {template} | {minutes} | {diet} | {len(r.ingredients)} | "
                f"{len(page['wikitext'])} | {cats}"
            )
    return 0


def cmd_build(api: Api, manifest_path: Path, lock_path: Path, out_path: Path, refresh: bool) -> int:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    wanted = [entry["title"] for entry in manifest["pages"]]
    lock: dict[str, dict[str, int]] = {}
    if lock_path.exists() and not refresh:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    locked = [t for t in wanted if t in lock]
    fresh = [t for t in wanted if t not in lock]
    pages: list[dict[str, Any]] = []
    if locked:
        pages += api.pages(revids=[lock[t]["revision_id"] for t in locked])
    if fresh:
        pages += api.pages(titles=fresh)
    recipes = sorted((to_recipe(p) for p in pages), key=lambda r: r.id)

    ids = [r.id for r in recipes]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        print(f"error: duplicate ids {duplicates}", file=sys.stderr)
        return 1
    got = {p["title"] for p in pages}
    missing = [t for t in wanted if t not in got]
    if missing:
        print(
            "error: manifest titles not returned by the API (use the canonical title printed "
            f"by --list): {missing}",
            file=sys.stderr,
        )
        return 1

    new_lock = {
        p["title"]: {"page_id": p["page_id"], "revision_id": p["revision_id"]} for p in pages
    }
    lock_path.write_text(
        json.dumps(new_lock, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    dumped = [r.model_dump() for r in recipes]
    corpus = Corpus(
        corpus_version=compute_version(dumped),
        source=SOURCE,
        license=LICENSE,
        built_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        recipes=recipes,
    )
    out_path.write_text(corpus.model_dump_json(indent=1) + "\n", encoding="utf-8")
    with_time = sum(r.total_minutes is not None for r in recipes)
    with_diet = sum("yes" in r.diet.values() for r in recipes)
    no_template = sum(not r.has_summary_template for r in recipes)
    print(
        f"wrote {len(recipes)} recipes to {out_path.relative_to(ROOT)} "
        f"(version {corpus.corpus_version}); with time: {with_time}, diet yes: {with_diet}, "
        f"without template: {no_template}; lock: {lock_path.relative_to(ROOT)}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--list", nargs="+", metavar="CATEGORY", help="print candidates of these categories"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="ignore the lock and fetch latest revisions"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    api = Api()
    if args.list:
        return cmd_list(api, args.list)
    return cmd_build(api, args.manifest, args.lock, args.out, args.refresh)


if __name__ == "__main__":
    sys.exit(main())
