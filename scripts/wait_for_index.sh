#!/usr/bin/env bash
# Wait until a package index's simple page lists vibe-seller <version>.
#
# Matches the exact wheel file name, never the bare version string: the
# page carries other text that contains it — a yank reason ("superseded
# by 0.3.1") or a longer version (0.3.1.dev20261112) — and a substring
# match let the 0.3.1 release audit start before 0.3.1 was installable.
#
# Usage: scripts/wait_for_index.sh <simple-index-url> <version>
#   WAIT_TRIES  attempts, 2 s apart (default 60)
set -u

INDEX=${1:?usage: wait_for_index.sh <simple-index-url> <version>}
VERSION=${2:?usage: wait_for_index.sh <simple-index-url> <version>}
TRIES=${WAIT_TRIES:-60}
WHEEL="vibe_seller-${VERSION}-py3-none-any.whl"

echo "Waiting for $WHEEL at $INDEX..."
for i in $(seq 1 "$TRIES"); do
  if curl -fsS "$INDEX" 2>/dev/null | grep -qF "$WHEEL"; then
    echo "  listed after $i tries"
    exit 0
  fi
  [ "$i" -lt "$TRIES" ] && sleep 2
done
echo "ERROR: $WHEEL not listed on $INDEX after $TRIES tries"
exit 1
