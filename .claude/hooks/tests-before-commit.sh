#!/usr/bin/env bash
# PreToolUse hook (Bash): run the fast unit tests before any `git commit`.
# Exit 2 blocks the commit and reports the failing tests. No-op until tests exist.
set -u
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
printf '%s' "$cmd" | grep -Eq '\bgit\b[^|;&]*\bcommit\b' || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
{ [ -f pyproject.toml ] && [ -d tests ]; } || exit 0
if ! out=$(uv run --frozen pytest -q -x tests 2>&1); then
  {
    echo "Blocked: unit tests fail; fix them before committing."
    printf '%s\n' "$out" | tail -n 30
  } >&2
  exit 2
fi
exit 0
