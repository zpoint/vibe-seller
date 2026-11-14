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
# And seeing it listed is still not the installer seeing it. PyPI's CDN
# answers from many edge caches, and one response listing the wheel says
# nothing about the next: the v0.3.2 release audit's wait saw the wheel in
# both representations on its first try, and `uv tool install
# vibe-seller==0.3.2` seconds later still found no such version — about a
# minute later the same resolution succeeded. So when uv is on PATH the
# wait ends only when uv ITSELF resolves the exact version from the index
# (no cache) WAIT_STABLE times in a row, a few seconds apart; the curl
# checks above stay as the cheap first filter (and are all a caller
# without uv gets).
#
# Usage: scripts/wait_for_index.sh <simple-index-url> <version>
#   WAIT_TRIES   attempts, 2 s apart (default 90)
#   WAIT_STABLE  consecutive uv resolutions required (default 3)
set -u

INDEX=${1:?usage: wait_for_index.sh <simple-index-url> <version>}
VERSION=${2:?usage: wait_for_index.sh <simple-index-url> <version>}
TRIES=${WAIT_TRIES:-90}
STABLE=${WAIT_STABLE:-3}
# https://pypi.org/simple/vibe-seller/ -> https://pypi.org/simple/
INDEX_ROOT="${INDEX%/}"
INDEX_ROOT="${INDEX_ROOT%/vibe-seller}/"
WHEEL="vibe_seller-${VERSION}-py3-none-any.whl"
# The Accept header uv sends for a simple index (PEP 691).
UV_ACCEPT='application/vnd.pypi.simple.v1+json, application/vnd.pypi.simple.v1+html;q=0.2, text/html;q=0.01'

listed() { # listed <accept-header>
  curl -fsS -H "Accept: $1" "$INDEX" 2>/dev/null | grep -qF "$WHEEL"
}

# The installer's own answer: resolve exactly this version, no cache, no
# dependencies (they are not what just got published).
resolves() {
  printf 'vibe-seller==%s\n' "$VERSION" \
    | uv pip compile --quiet --no-cache --no-deps --python-version 3.11 \
        --index-url "$INDEX_ROOT" - >/dev/null 2>&1
}

if command -v uv >/dev/null 2>&1; then
  have_uv=1
else
  have_uv=0
  STABLE=1
fi

echo "Waiting for $WHEEL at $INDEX..."
ok=0
for i in $(seq 1 "$TRIES"); do
  if listed 'text/html' && listed "$UV_ACCEPT" \
      && { [ "$have_uv" = 0 ] || resolves; }; then
    ok=$((ok + 1))
    if [ "$ok" -ge "$STABLE" ]; then
      if [ "$have_uv" = 1 ]; then
        echo "  listed and resolved by uv $ok times in a row (try $i)"
      else
        echo "  listed after $i tries"
      fi
      exit 0
    fi
  else
    ok=0
  fi
  [ "$i" -lt "$TRIES" ] && sleep 2
done
what=listed
[ "$have_uv" = 1 ] && what='listed and resolved by uv'
echo "ERROR: $WHEEL not $what on $INDEX after $TRIES tries"
exit 1
