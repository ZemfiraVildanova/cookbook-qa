#!/usr/bin/env bash
# PostToolUse hook (Edit|Write): format a Python file right after it is edited.
# No-op until the uv project exists. Never blocks.
set -u
input=$(cat)
file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$file" in
  *.py) ;;
  *) exit 0 ;;
esac
[ -f "$file" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
[ -f pyproject.toml ] || exit 0
uv run --frozen ruff format --quiet "$file" >/dev/null 2>&1 || true
exit 0
