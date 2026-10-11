"""End-to-end eval harness: the golden set against a running service (SPEC 12.3).

Run: `uv run python evals/run_evals.py --target http://localhost:8000 --runs 1 [--label ci]`

For every response: contract validation with `AskResponse` from `schemas.py` (which enforces
I-01..I-04 and I-08), I-05 and I-07 against the committed corpus, then the case assertions.
The three probes check the 422 contract and the `X-Request-ID` header (R-71). A case passes
only if it passes in every run. The JSON report (per-case latency, tokens, cost from
`evals/prices.yaml`) goes to `evals/reports/`; the exit code is non-zero on any failure.
I-06 needs the request log and is covered by the server-guard unit tests, not by the harness.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import ValidationError

from recipe_qa.config import (
    EMBEDDING_MODEL,
    QUESTION_MAX_CHARS,
    REFUSAL_TEMPLATES,
    SAFETY_DISCLAIMER,
)
from recipe_qa.constraints import recipe_passes
from recipe_qa.corpus import Corpus, Recipe, load_corpus
from recipe_qa.schemas import AppliedConstraints, AskResponse

ROOT = Path(__file__).resolve().parent.parent
ANSWER_EXCERPT_CHARS = 400  # report readability only; not a behavior constant


class Prices:
    """USD per token by model name, from evals/prices.yaml (vendor page and date inside)."""

    def __init__(self, path: Path) -> None:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.checked: str = str(data["checked"])
        self.models: dict[str, dict[str, Any]] = data["models"]

    def cost(self, model: str | None, input_tokens: int, output_tokens: int) -> float | None:
        if model is None:
            return 0.0
        row = self.models.get(model)
        if row is None:
            return None
        per_in = float(row["input_per_mtok"]) / 1_000_000
        per_out = float(row["output_per_mtok"]) / 1_000_000
        return input_tokens * per_in + output_tokens * per_out

    def embedding_upper_bound(self) -> float | None:
        """Cost of embedding a QUESTION_MAX_CHARS question (about 4 chars per token)."""
        row = self.models.get(EMBEDDING_MODEL)
        if row is None:
            return None
        tokens = math.ceil(QUESTION_MAX_CHARS / 4)
        return tokens * float(row["input_per_mtok"]) / 1_000_000


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def invariant_failures(resp: AskResponse, by_id: dict[str, Recipe]) -> list[str]:
    """I-05, I-07, I-08 and R-23 (I-01..I-04 and I-08 are also enforced by AskResponse itself)."""
    failures: list[str] = []
    ids = [c.recipe_id for c in resp.citations]
    if len(set(ids)) != len(ids):
        failures.append("I-05: duplicate citation")
    for c in resp.citations:
        recipe = by_id.get(c.recipe_id)
        if recipe is None:
            failures.append(f"I-05: unknown recipe id {c.recipe_id}")
            continue
        if c.title != recipe.title or c.url != recipe.url:
            failures.append(f"I-05: title/url differ from corpus for {c.recipe_id}")
        if not resp.refused and not recipe_passes(recipe, resp.applied_constraints):
            failures.append(f"I-07: {c.recipe_id} violates applied_constraints")
    if resp.refused and resp.refusal_reason is not None:  # R-23
        reason = resp.refusal_reason.value
        template_ok = (
            (resp.answer or "").startswith(SAFETY_DISCLAIMER)
            if reason == "safety"
            else resp.answer == REFUSAL_TEMPLATES[reason]
        )
        if not template_ok:
            failures.append(f"R-23: refusal text is not the {reason} template")
    no_model = resp.meta.model is None
    usage_zero = resp.meta.usage.input_tokens == 0 and resp.meta.usage.output_tokens == 0
    if no_model != (resp.meta.prompt_version is None) or no_model != usage_zero:
        failures.append("I-08: model, prompt_version and usage disagree")
    return failures


def assertion_failures(
    expect: dict[str, Any], status: int, resp: AskResponse | None, by_id: dict[str, Recipe]
) -> list[str]:
    failures: list[str] = []
    cited = [c.recipe_id for c in resp.citations] if resp else []
    for key, value in expect.items():
        ok: bool
        if key == "http_status":
            ok = status == value
        elif resp is None:
            ok = False
        elif key == "refused":
            ok = resp.refused is value
        elif key == "refusal_reason":
            ok = (resp.refusal_reason.value if resp.refusal_reason else None) == value
        elif key == "cites_any":
            ok = any(rid in cited for rid in value)
        elif key == "cites_all":
            ok = all(rid in cited for rid in value)
        elif key == "no_citations":
            ok = (not cited) is value
        elif key == "min_citations":
            ok = len(cited) >= int(value)
        elif key == "constraints_satisfied":
            ok = all(
                recipe_passes(by_id[rid], resp.applied_constraints) for rid in cited if rid in by_id
            ) is value and bool(cited)
        elif key == "applied_constraints":
            ok = AppliedConstraints(**value) == resp.applied_constraints
        elif key == "answer_contains_any":
            answer = (resp.answer or "").lower()
            ok = any(str(s).lower() in answer for s in value)
        elif key == "model_called":
            ok = (resp.meta.model is not None) is value
        else:
            failures.append(f"unknown assertion {key}")
            continue
        if not ok:
            failures.append(f"{key}: expected {value!r}")
    return failures


def post_ask(client: httpx.Client, body: str, run: int) -> tuple[httpx.Response, float]:
    started = time.perf_counter()
    response = client.post(
        "/ask",
        content=body,
        headers={"content-type": "application/json", "x-eval-run": str(run)},
    )
    return response, (time.perf_counter() - started) * 1000


def run_case(
    client: httpx.Client, case: dict[str, Any], run: int, by_id: dict[str, Recipe], prices: Prices
) -> dict[str, Any]:
    body = json.dumps({"question": case["question"]})
    response, wall_ms = post_ask(client, body, run)
    record: dict[str, Any] = {
        "run": run,
        "http_status": response.status_code,
        "latency_ms": round(wall_ms),
        "request_id": response.headers.get("x-request-id"),
        "failures": [],
    }
    resp: AskResponse | None = None
    if response.status_code == 200:
        try:
            resp = AskResponse.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            record["failures"].append(f"schema: {str(exc).splitlines()[0]}")
    else:
        record["failures"].append(f"http_status: {response.status_code}")
        record["body"] = response.text[:ANSWER_EXCERPT_CHARS]
    if resp is not None:
        record["failures"].extend(invariant_failures(resp, by_id))
        record.update(
            {
                "server_latency_ms": resp.meta.latency_ms,
                "refused": resp.refused,
                "refusal_reason": resp.refusal_reason.value if resp.refusal_reason else None,
                "cited_ids": [c.recipe_id for c in resp.citations],
                "applied_constraints": resp.applied_constraints.model_dump(exclude_defaults=True),
                "model": resp.meta.model,
                "retrieval_mode": resp.meta.retrieval_mode.value,
                "input_tokens": resp.meta.usage.input_tokens,
                "output_tokens": resp.meta.usage.output_tokens,
                "cost_usd": prices.cost(
                    resp.meta.model, resp.meta.usage.input_tokens, resp.meta.usage.output_tokens
                ),
                "answer": (resp.answer or "")[:ANSWER_EXCERPT_CHARS],
            }
        )
        if record["cost_usd"] is None:
            record["failures"].append(f"price: no row for model {resp.meta.model} in prices.yaml")
    record["failures"].extend(assertion_failures(case["expect"], response.status_code, resp, by_id))
    record["passed"] = not record["failures"]
    return record


def run_probe(client: httpx.Client, probe: dict[str, Any], run: int) -> dict[str, Any]:
    if "raw_body" in probe:
        body = probe["raw_body"]
    elif "question_chars" in probe:
        body = json.dumps({"question": "q" * int(probe["question_chars"])})
    else:
        body = json.dumps({"question": probe["question"]})
    response, wall_ms = post_ask(client, body, run)
    failures: list[str] = []
    expected = probe["expect"].get("http_status")
    if response.status_code != expected:
        failures.append(f"http_status: expected {expected}, got {response.status_code}")
    if not response.headers.get("x-request-id"):
        failures.append("R-71: X-Request-ID header missing")
    return {
        "run": run,
        "http_status": response.status_code,
        "latency_ms": round(wall_ms),
        "request_id": response.headers.get("x-request-id"),
        "failures": failures,
        "passed": not failures,
    }


def summarise(
    cases: list[dict[str, Any]], probes: list[dict[str, Any]], prices: Prices
) -> dict[str, Any]:
    def verdict(item: dict[str, Any]) -> str:
        outcomes = [r["passed"] for r in item["runs"]]
        if all(outcomes):
            return "pass"
        return "flaky" if any(outcomes) else "fail"

    for item in [*cases, *probes]:
        item["result"] = verdict(item)
    model_latency = [
        float(r["latency_ms"])
        for c in cases
        for r in c["runs"]
        if r.get("http_status") == 200 and r.get("model")
    ]
    pre_llm_latency = [
        float(r["latency_ms"])
        for c in cases
        for r in c["runs"]
        if r.get("http_status") == 200 and not r.get("model")
    ] + [float(r["latency_ms"]) for p in probes for r in p["runs"]]
    server_model_latency = [
        float(r["server_latency_ms"]) for c in cases for r in c["runs"] if r.get("model")
    ]
    costs = [r["cost_usd"] for c in cases for r in c["runs"] if r.get("cost_usd") is not None]
    per_question = (sum(costs) / len(costs)) if costs else None
    return {
        "cases_total": len(cases),
        "cases_passed": sum(c["result"] == "pass" for c in cases),
        "cases_flaky": sum(c["result"] == "flaky" for c in cases),
        "cases_failed": sum(c["result"] == "fail" for c in cases),
        "probes_passed": sum(p["result"] == "pass" for p in probes),
        "probes_total": len(probes),
        "latency_ms_model_answers": {
            "n": len(model_latency),
            "p50": percentile(model_latency, 50),
            "p95": percentile(model_latency, 95),
            "server_p50": percentile(server_model_latency, 50),
            "server_p95": percentile(server_model_latency, 95),
        },
        "latency_ms_pre_llm": {
            "n": len(pre_llm_latency),
            "p50": percentile(pre_llm_latency, 50),
            "max": max(pre_llm_latency) if pre_llm_latency else None,
        },
        "cost_usd_per_question": per_question,
        "cost_usd_per_1000_questions": per_question * 1000 if per_question is not None else None,
        "embedding_cost_usd_upper_bound_per_question": prices.embedding_upper_bound(),
        "prices_checked": prices.checked,
    }


def print_table(cases: list[dict[str, Any]], probes: list[dict[str, Any]]) -> None:
    print(f"{'id':6} {'result':6} {'ms(p50)':>8}  {'outcome':28} failures")
    for item in [*cases, *probes]:
        runs = item["runs"]
        p50 = percentile([float(r["latency_ms"]) for r in runs], 50) or 0
        last = runs[-1]
        if "refused" in last:
            outcome = (
                f"refused/{last['refusal_reason']}"
                if last["refused"]
                else f"answered {','.join(last['cited_ids'])}"
            )
        else:
            outcome = f"http {last['http_status']}"
        failures = "; ".join(f for r in runs for f in r["failures"])
        print(f"{item['id']:6} {item['result']:6} {p50:8.0f}  {outcome[:28]:28} {failures}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="base URL of the service")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--golden", type=Path, default=ROOT / "evals" / "golden.yaml")
    parser.add_argument("--prices", type=Path, default=ROOT / "evals" / "prices.yaml")
    parser.add_argument("--corpus", type=Path, default=ROOT / "data" / "corpus.json")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "evals" / "reports")
    parser.add_argument("--label", default=None, help="report name suffix (default: host)")
    parser.add_argument("--only", default=None, help="comma-separated case ids")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)

    golden = yaml.safe_load(args.golden.read_text(encoding="utf-8"))
    cases_spec: list[dict[str, Any]] = golden["cases"]
    probes_spec: list[dict[str, Any]] = golden["probes"]
    if args.only:
        wanted = set(args.only.split(","))
        cases_spec = [c for c in cases_spec if c["id"] in wanted]
        probes_spec = [p for p in probes_spec if p["id"] in wanted]
    corpus: Corpus = load_corpus(args.corpus)
    by_id = {r.id: r for r in corpus.recipes}
    prices = Prices(args.prices)

    base = args.target.rstrip("/")
    failures_global: list[str] = []
    with httpx.Client(base_url=base, timeout=args.timeout) as client:
        health = client.get("/health").json()
        if health.get("corpus_version") != corpus.corpus_version:
            failures_global.append(
                f"corpus_version: service {health.get('corpus_version')} != local "
                f"{corpus.corpus_version}; I-05/I-07 would be checked against the wrong corpus"
            )
        cases = [
            {"id": c["id"], "group": c["group"], "question": c["question"], "runs": []}
            for c in cases_spec
        ]
        probes = [{"id": p["id"], "runs": []} for p in probes_spec]
        for run in range(1, args.runs + 1):
            for spec, item in zip(cases_spec, cases, strict=True):
                item["runs"].append(run_case(client, spec, run, by_id, prices))
            for spec, item in zip(probes_spec, probes, strict=True):
                item["runs"].append(run_probe(client, spec, run))

    summary = summarise(cases, probes, prices)
    summary["global_failures"] = failures_global
    report = {
        "target": base,
        "git_sha": health.get("git_sha"),
        "corpus_version": health.get("corpus_version"),
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
        "runs": args.runs,
        "summary": summary,
        "cases": cases,
        "probes": probes,
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    label = args.label or re.sub(r"[^a-z0-9]+", "-", httpx.URL(base).host.lower()).strip("-")
    path = args.report_dir / f"{stamp}-{label}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print_table(cases, probes)
    lm = summary["latency_ms_model_answers"]
    print(
        f"\ncases {summary['cases_passed']}/{summary['cases_total']} pass"
        f" ({summary['cases_flaky']} flaky, {summary['cases_failed']} fail),"
        f" probes {summary['probes_passed']}/{summary['probes_total']};"
        f" model answers p50 {lm['p50']} ms p95 {lm['p95']} ms (n={lm['n']});"
        f" pre-LLM p50 {summary['latency_ms_pre_llm']['p50']} ms;"
        f" cost/question ${summary['cost_usd_per_question'] or 0:.6f}"
        f" (${summary['cost_usd_per_1000_questions'] or 0:.4f} per 1,000)"
    )
    for failure in failures_global:
        print(f"FAIL {failure}")
    print(f"report: {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    ok = (
        not failures_global
        and summary["cases_passed"] == summary["cases_total"]
        and summary["probes_passed"] == summary["probes_total"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
