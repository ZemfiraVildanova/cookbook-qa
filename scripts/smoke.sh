#!/usr/bin/env bash
# Production smoke (SPEC 11): /health, then three questions through the whole pipeline:
# an answer with a citation, an out_of_domain refusal, a safety refusal without a model call.
# Usage: scripts/smoke.sh https://host   (exit 1 on the first failed case)
# Each case gets up to SMOKE_ATTEMPTS tries (default 2) so a single transient 503 does not
# fail a scheduled run; a failure that repeats does.
set -euo pipefail

BASE="${1:?usage: smoke.sh BASE_URL}"
BASE="${BASE%/}"
ATTEMPTS="${SMOKE_ATTEMPTS:-2}"

health=$(curl -fsS -m 15 "$BASE/health")
echo "health: $health"
echo "$health" | jq -e '.status == "ok" and (.git_sha | length) > 0' >/dev/null

# name | question | jq predicate over the 200 body
cases=(
  'answer|How do I make carbonara?|.refused == false and (.citations | map(.recipe_id) | index("spaghetti-alla-carbonara")) != null and .meta.model != null'
  'refusal|What is the capital of France?|.refused == true and .refusal_reason == "out_of_domain" and .citations == []'
  'safety|Is the carbonara nut-free?|.refused == true and .refusal_reason == "safety" and .meta.model == null and (.citations | map(.recipe_id)) == ["spaghetti-alla-carbonara"]'
)

for entry in "${cases[@]}"; do
  IFS='|' read -r name question predicate <<<"$entry"
  ok=0
  for ((try = 1; try <= ATTEMPTS; try++)); do
    body=$(jq -cn --arg q "$question" '{question: $q}' |
      curl -sS -m 60 -X POST "$BASE/ask" -H 'content-type: application/json' -d @- || true)
    if echo "$body" | jq -e "$predicate" >/dev/null 2>&1; then
      ok=1
      echo "smoke $name: ok (attempt $try, $(echo "$body" | jq -r '.meta.latency_ms') ms, request $(echo "$body" | jq -r '.meta.request_id'))"
      break
    fi
    echo "smoke $name: attempt $try failed: $(echo "$body" | cut -c1-300)" >&2
    sleep 3
  done
  if [ "$ok" -ne 1 ]; then
    echo "smoke $name: FAILED after $ATTEMPTS attempts" >&2
    exit 1
  fi
done
echo "smoke: all cases passed against $BASE"
