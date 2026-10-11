#!/usr/bin/env bash
# Deploy the service to Northflank from the committed template, idempotently.
#
#   1. Create the template if it does not exist, otherwise update it from northflank-template.json.
#   2. Run it with the image tag and the keys as run arguments (the committed file holds names only).
#   3. Wait for the run to conclude, then for /health on the public URL to report GIT_SHA.
#
# Required env: IMAGE, GIT_SHA, NORTHFLANK_API_TOKEN, ANTHROPIC_API_KEY, OPENAI_API_KEY.
# Optional env: TEMPLATE_FILE, PROJECT_ID, SERVICE_ID, PORT_NAME, PUBLIC_URL,
#               RUN_TIMEOUT_S (default 900), HEALTH_TIMEOUT_S (default 600).
# Secrets reach curl through a private config file, never on the command line; API responses
# are never printed raw, only selected fields, because a run response can echo arguments.
set -euo pipefail

API="https://api.northflank.com/v1"
TEMPLATE_FILE="${TEMPLATE_FILE:-northflank-template.json}"
PROJECT_ID="${PROJECT_ID:-cookbook-qa}"
SERVICE_ID="${SERVICE_ID:-web}"
PORT_NAME="${PORT_NAME:-p01}"
RUN_TIMEOUT_S="${RUN_TIMEOUT_S:-900}"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-600}"

for v in IMAGE GIT_SHA NORTHFLANK_API_TOKEN ANTHROPIC_API_KEY OPENAI_API_KEY; do
  [ -n "${!v:-}" ] || { echo "bootstrap: $v is required" >&2; exit 2; }
done
command -v jq >/dev/null || { echo "bootstrap: jq is required" >&2; exit 2; }

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
curlrc="$workdir/curlrc"
(
  umask 077
  printf 'header = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\n' \
    "$NORTHFLANK_API_TOKEN" > "$curlrc"
)

HTTP_CODE=000
# nf METHOD PATH [BODY_FILE]: prints the response body, sets HTTP_CODE.
nf() {
  local method=$1 path=$2 body=${3:-}
  local out="$workdir/resp"
  local -a extra=()
  if [ -n "$body" ]; then extra=(--data-binary "@$body"); fi
  HTTP_CODE=$(curl -sS -m 60 -K "$curlrc" -X "$method" "${extra[@]}" -o "$out" \
    -w '%{http_code}' "$API$path") || HTTP_CODE=000
  cat "$out"
}
ok() { [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; }
# Print only error-ish fields of a response, never arguments or specs.
brief() { jq -c '{error: (.error // null), message: (.message // null), status: (.data.status // null)}' 2>/dev/null || echo '{"unparsed":true}'; }

name=$(jq -r .name "$TEMPLATE_FILE")
echo "bootstrap: template '$name', image $IMAGE, expected sha $GIT_SHA"

# --- 1. create or update the template ---------------------------------------------------
existing=$(nf GET "/templates?per_page=100" \
  | jq -r --arg n "$name" '(.data.templates // [])[] | select(.name == $n) | .id' 2>/dev/null \
  | head -n1 || true)
if [ -n "$existing" ]; then
  resp=$(nf POST "/templates/$existing" "$TEMPLATE_FILE")
  template_id="$existing"
  action=updated
else
  resp=$(nf POST "/templates" "$TEMPLATE_FILE")
  template_id=$(printf '%s' "$resp" | jq -r '.data.id // empty')
  action=created
fi
if ! ok || [ -z "$template_id" ]; then
  echo "bootstrap: template $action failed (HTTP $HTTP_CODE): $(printf '%s' "$resp" | brief)" >&2
  exit 1
fi
echo "bootstrap: template $action, id $template_id"

# --- 2. run it ----------------------------------------------------------------------------
jq -n --arg image "$IMAGE" --arg a "$ANTHROPIC_API_KEY" --arg o "$OPENAI_API_KEY" \
  '{arguments: {IMAGE: $image, ANTHROPIC_API_KEY: $a, OPENAI_API_KEY: $o}}' > "$workdir/run.json"
resp=$(nf POST "/templates/$template_id/runs" "$workdir/run.json")
run_id=$(printf '%s' "$resp" | jq -r '.data.id // .data.templateRun.id // empty')
if ! ok || [ -z "$run_id" ]; then
  echo "bootstrap: template run did not start (HTTP $HTTP_CODE): $(printf '%s' "$resp" | brief)" >&2
  exit 1
fi
echo "bootstrap: run $run_id started"

# --- 3. wait for the run ------------------------------------------------------------------
deadline=$(( $(date +%s) + RUN_TIMEOUT_S ))
while :; do
  resp=$(nf GET "/templates/$template_id/runs/$run_id")
  status=$(printf '%s' "$resp" | jq -r '.data.status // .data.templateRun.status // "unknown"')
  concluded=$(printf '%s' "$resp" | jq -r '.data.concluded // .data.templateRun.concluded // false')
  echo "bootstrap: run status $status"
  if [ "$status" = "success" ]; then
    break
  fi
  if [ "$concluded" = "true" ] || [ "$status" = "failure" ] || [ "$status" = "aborted" ]; then
    echo "bootstrap: run ended with status $status; step statuses:" >&2
    printf '%s' "$resp" \
      | jq -c '[.. | objects | select(has("kind") and has("status")) | {kind, status}]' >&2 || true
    exit 1
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "bootstrap: timed out after ${RUN_TIMEOUT_S}s waiting for the run" >&2
    exit 1
  fi
  sleep 10
done

# --- 4. public URL ------------------------------------------------------------------------
if [ -z "${PUBLIC_URL:-}" ]; then
  resp=$(nf GET "/projects/$PROJECT_ID/services/$SERVICE_ID")
  dns=$(printf '%s' "$resp" \
    | jq -r --arg p "$PORT_NAME" '(.data.ports // [])[] | select(.name == $p) | .dns // empty' \
    | head -n1)
  if [ -z "$dns" ]; then
    echo "bootstrap: could not read the public DNS (HTTP $HTTP_CODE); set PUBLIC_URL" >&2
    exit 1
  fi
  PUBLIC_URL="https://$dns"
fi
echo "bootstrap: public URL $PUBLIC_URL"

# --- 5. wait for the new revision ----------------------------------------------------------
deadline=$(( $(date +%s) + HEALTH_TIMEOUT_S ))
while :; do
  sha=$(curl -sS -m 10 "$PUBLIC_URL/health" 2>/dev/null | jq -r '.git_sha // empty' 2>/dev/null || true)
  if [ "$sha" = "$GIT_SHA" ]; then
    echo "bootstrap: /health reports $sha"
    break
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "bootstrap: /health did not report $GIT_SHA within ${HEALTH_TIMEOUT_S}s (last: '${sha:-none}')" >&2
    exit 1
  fi
  sleep 10
done

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "url=$PUBLIC_URL" >> "$GITHUB_OUTPUT"
fi
echo "bootstrap: done"
