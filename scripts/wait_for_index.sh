#!/usr/bin/env bash
# Wait until a package index's simple page lists vibe-seller <version>.
#
# Matches the exact wheel file name, never the bare version string: the
# page carries other text that contains it — a yank reason ("superseded
# by 0.3.1") or a longer version (0.3.1.dev20261112) — and a substring
# match let the 0.3.1 release audit start before 0.3.1 was installable.
#
# And it waits for the page the INSTALLER will read, not just the one a
# browser gets. PyPI serves the simple index in two representations,
# chosen by the Accept header — HTML, and the PEP 691 JSON that uv asks
# for — and its CDN caches them apart. A nightly's wait saw the wheel on
# the HTML page on the first try while uv, seconds later, was still
# served a JSON page without it ("there is no version of
# vibe-seller==0.3.2.dev…"). So the wheel must be listed in both.
#
# Usage: scripts/wait_for_index.sh <simple-index-url> <version>
#   WAIT_TRIES  attempts, 2 s apart (default 60)
set -u

INDEX=${1:?usage: wait_for_index.sh <simple-index-url> <version>}
VERSION=${2:?usage: wait_for_index.sh <simple-index-url> <version>}
TRIES=${WAIT_TRIES:-60}
WHEEL="vibe_seller-${VERSION}-py3-none-any.whl"
# The Accept header uv sends for a simple index (PEP 691).
UV_ACCEPT='application/vnd.pypi.simple.v1+json, application/vnd.pypi.simple.v1+html;q=0.2, text/html;q=0.01'

listed() { # listed <accept-header>
  curl -fsS -H "Accept: $1" "$INDEX" 2>/dev/null | grep -qF "$WHEEL"
}

echo "Waiting for $WHEEL at $INDEX..."
for i in $(seq 1 "$TRIES"); do
  if listed 'text/html' && listed "$UV_ACCEPT"; then
    echo "  listed after $i tries"
    exit 0
  fi
  [ "$i" -lt "$TRIES" ] && sleep 2
done
echo "ERROR: $WHEEL not listed on $INDEX after $TRIES tries"
exit 1
