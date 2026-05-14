#!/usr/bin/env bash
# Fail if any file passed as an argument exceeds 800 lines.
# Used by the `line-limit` pre-commit hook.
set -euo pipefail

MAX=800
fail=0
for f in "$@"; do
  [ -f "$f" ] || continue
  n=$(wc -l <"$f" | tr -d ' ')
  if [ "$n" -gt "$MAX" ]; then
    echo "$f: $n lines (max $MAX) — split into smaller modules" >&2
    fail=1
  fi
done
exit "$fail"
