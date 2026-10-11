#!/usr/bin/env bash
# commit-msg hook: enforce the commit format and reject AI attribution lines.
# Format: <prefix>: <summary>, prefix in docs|chore|test|feat|fix|data|eval|ci|build.
set -eu
msg_file="$1"
# Drop comment lines git adds to the editor template.
body=$(grep -v '^#' "$msg_file" || true)
subject=$(printf '%s\n' "$body" | sed -n '1p')

if ! printf '%s' "$subject" | grep -Eq '^(docs|chore|test|feat|fix|data|eval|ci|build)(\([a-z0-9/_.-]+\))?: .+'; then
  echo "commit-msg: subject must be '<prefix>: <summary>' with prefix docs|chore|test|feat|fix|data|eval|ci|build" >&2
  echo "commit-msg: got: $subject" >&2
  exit 1
fi

if printf '%s\n' "$body" | grep -Eiq '^[[:space:]]*co-authored-by:|generated with|claude code|🤖'; then
  echo "commit-msg: attribution lines (Co-Authored-By, Generated with, Claude Code) are not allowed" >&2
  exit 1
fi
exit 0
