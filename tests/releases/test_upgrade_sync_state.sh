#!/usr/bin/env bash
# Upgrade test: an install that predates the central sync scheduler
# must keep working — its per-module sync bookkeeping is imported into
# sync_state, the legacy schedule keys are gone from disk, inventory
# survives, and nothing else (stores, profiles, schedules) notices.
#
#   1. install the PREVIOUS stable from PyPI into an isolated tool env
#   2. boot it on an isolated VIBE_HOME, create a store, and leave the
#      legacy meta files behind (that is all an old client ever wrote)
#   3. install the CURRENT build over the SAME VIBE_HOME and boot it
#   4. assert the migration happened, and that booting twice is a no-op
#
# The sandbox is created HERE and nowhere else, and only the sandbox is
# ever deleted. In particular this script ignores an exported
# ``VIBE_HOME`` — that is the app's own data-directory variable, and
# reading it would boot an old release against somebody's real install
# and then migrate over it. The one ``rm -rf`` is guarded by a marker
# file this run wrote and by a path check.
#
# Usage: tests/releases/test_upgrade_sync_state.sh [previous-version]
#   PREV_VERSION   default: latest stable on PyPI
#   PORT           default: 7823
#
# Bash, not zsh (#205): the release gate runs this on ubuntu-latest,
# where zsh is not installed. The port changed no logic — bash passes
# an unmatched glob through literally (zsh aborts), so stop_instance
# keeps its ``[ -f "$f" ] || continue`` guard and drops the zsh-only
# ``(N)`` null-glob qualifiers.
set -u

PREV_VERSION=${1:-}
PORT=${PORT:-7823}
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
PY=$REPO/.venv/bin/python
FAKE_PORT=${FAKE_PORT:-7814}
fail() { echo "FAIL: $*" >&2; exit 1; }

# Stop ONLY the instance we started. ``stop.sh`` also stops port
# clients and reaps every task agent, so a test must never call it:
# SIGTERM the pid file the starter wrote, then wait for the exit.
stop_instance() {
  # Every pid file the sandbox knows about: the layout differs between
  # releases (the shell starter writes .pids_<port> in the worktree, the
  # installed CLI writes under $VIBE_HOME/pids/), and everything under
  # OUR sandbox is ours to stop.
  local pid f
  # Unmatched globs stay literal in bash, and the -f guard skips them
  # (zsh needed ``(N)`` for the same effect).
  for f in "$REPO/.pids_$PORT" "$VH"/pids/*.pid "$VH"/pids/*"$PORT"*; do
    [ -f "$f" ] || continue
    pid=$(cat "$f" 2>/dev/null)
    echo "   stop: $f -> pid ${pid:-<none>}"
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null
      for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
    fi
    rm -f "$f"
  done
}

# ── sandbox: this run's, and nothing else's ──────────────────
WORK=$(mktemp -d "${TMPDIR:-/tmp}/vibe-upgrade-test.XXXXXX") \
  || fail "could not create the sandbox"
MARKER="$WORK/.owned-by-test_upgrade_sync_state"
touch "$MARKER"
VH="$WORK/home"
mkdir -p "$VH"

rm_work() {
  # The ONLY deletion in this script, and only for our own sandbox.
  case "$WORK" in
    "${TMPDIR:-/tmp}"/vibe-upgrade-test.*) ;;
    *)
      echo "refusing to remove '$WORK': not this run's sandbox" >&2
      return 1
      ;;
  esac
  if [ ! -f "$MARKER" ]; then
    echo "refusing to remove '$WORK': marker file missing" >&2
    return 1
  fi
  rm -rf "$WORK"
}

port_free() {
  ! curl -fsS --noproxy '*' --max-time 2 \
    "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1
}

expect_port_free() {
  # The whole test is meaningless if a previous instance still answers:
  # the "upgrade" would then be measured against the OLD server's
  # database (no sync_state table at all).
  local i
  for i in $(seq 1 20); do
    port_free && return 0
    sleep 1
  done
  echo "   pid files present:" >&2
  ls -l "$VH"/pids "$REPO"/.pids_$PORT 2>&1 | sed 's/^/     /' >&2
  fail "port $PORT is still serving after stop — aborting rather than \
testing the wrong server"
}

wait_for_health() {
  local i
  # --noproxy '*': a shell-level proxy (clash/VPN on 127.0.0.1) must
  # not intercept the loopback probe — same trap start.sh documents.
  for i in $(seq 1 60); do
    curl -fsS --noproxy '*' --max-time 2 \
      "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  fail "the server on port $PORT did not come up"
}

cleanup() {
  stop_instance
  rm_work
}
trap cleanup EXIT

expect_port_free || true   # (fails fast below, before anything starts)
if ! port_free; then
  fail "port $PORT is already serving something that is not ours — \
pick another port with PORT=<n>; refusing to test against it"
fi

echo "== 1. previous stable in its own venv"
# ``uv`` (not pip): the project venv is uv-managed and has no pip
# module, and CI creates it with ``uv venv`` + ``uv pip install``.
UV_BIN=${UV:-$(command -v uv || echo "$REPO/.venv/bin/uv")}
# Install the release with the dependencies it had on the day it
# shipped — what a user upgrading from it actually has. Resolving its
# open-ended ranges today pulls releases it never ran against (a newer
# browser-use stopped bringing in a module old releases imported
# without declaring, so every old release failed to start).
read -r PREV_RESOLVED PREV_CUTOFF < <(
  "$PY" - "${PREV_VERSION:-}" <<'PYEOF'
import datetime, json, sys, urllib.request
want = sys.argv[1]
with urllib.request.urlopen('https://pypi.org/pypi/vibe-seller/json') as r:
    meta = json.load(r)
ver = want or meta['info']['version']
files = meta['releases'][ver]
up = max(
    datetime.datetime.fromisoformat(f['upload_time_iso_8601'].replace('Z', '+00:00'))
    for f in files
)
print(ver, (up + datetime.timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ'))
PYEOF
) || fail "could not look up the previous release on PyPI"
"$UV_BIN" venv -q --python 3.11 "$WORK/old" >/dev/null \
  || fail "could not create the old venv"
OLD="$WORK/old/bin"
"$UV_BIN" pip install -q --python "$OLD/python" \
  --exclude-newer "$PREV_CUTOFF" "vibe-seller==$PREV_RESOLVED" \
  || fail "could not install the previous release"
OLD_VERSION=$("$OLD/vibe-seller" --version 2>/dev/null | tail -1)
echo "   previous: ${OLD_VERSION:-unknown}"

echo "== 2. boot the old client on an isolated home"
export VIBE_HOME=$VH   # ours — never taken from the caller
export VIBE_SYNC_DISABLED=1   # what a pre-0.2.x client reads
export VIBE_GITHUB_MAIN_SYNC=off
export VIBE_SYNC_JITTER=0
"$OLD/vibe-seller" start --port $PORT >/dev/null 2>&1 &
OLD_PID=$!
wait_for_health
# A store, so the upgrade has real state to keep.
curl -fsS --noproxy '*' -X POST "http://127.0.0.1:$PORT/api/stores" \
  -H 'Content-Type: application/json' \
  -d '{"name":"acme","platform":"amazon","countries":["US"],"browser_backend":"chrome"}' \
  >/dev/null || echo "   (store create not available on this version)"

# What an old client leaves behind: schedule state in three files.
mkdir -p "$VH/.claude/skills" "$VH/knowledge" "$VH/cache"
cat > "$VH/.claude/skills/.sync_meta.json" <<'JSON'
{
  "last_sync_at": "2026-11-01T02:03:04+00:00",
  "last_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "status": "success",
  "error": null,
  "copied": 3,
  "skipped": 7,
  "synced_skills": ["widget-006"],
  "gws_installed": true
}
JSON
cat > "$VH/knowledge/.sync_meta.json" <<'JSON'
{"last_sync_at": "2026-10-01T00:00:00+00:00", "last_commit": "bbbb", "status": "success"}
JSON
cat > "$VH/cache/provider_presets.json" <<'JSON'
{
  "sha256": "cccc",
  "etag": "W/\"v1\"",
  "fetched_at": "2026-11-02T00:00:00+00:00",
  "source_url": "https://example.com/presets.py",
  "presets": {},
  "models": {}
}
JSON
stop_instance
kill -TERM $OLD_PID 2>/dev/null
sleep 2
expect_port_free

echo "== 3. the current build over the same home"
expect_port_free
"$UV_BIN" pip install --python "$PY" -q -e "$REPO" \
  || fail "could not install this build"
export VIBE_GITHUB_MAIN_SYNC=off
export VIBE_EXTERNAL_SYNC=off   # the current build has two switches
"$REPO/start.sh" $PORT >/dev/null 2>&1
wait_for_health

echo "== 4. the migration"
"$PY" - <<PY
import json, sqlite3, sys, os
home = os.environ['VIBE_HOME']
db = sqlite3.connect(os.path.join(home, 'data', 'vibe_seller.db'))
rows = {
    name: row for name, *row in db.execute(
        'SELECT job_name, last_ok_at, version, status, detail '
        'FROM sync_state'
    )
}
assert 'skills' in rows, rows
assert rows['skills'][0] == '2026-11-01T02:03:04+00:00', rows['skills']
assert rows['skills'][1] == 'a' * 40, rows['skills']
assert 'knowledge' in rows, rows
assert 'presets' in rows, rows

skills = json.load(open(os.path.join(
    home, '.claude', 'skills', '.sync_meta.json')))
assert 'last_sync_at' not in skills, skills
assert 'last_commit' not in skills, skills
# The seed survives — and the boot's local sync MERGES the packaged
# skills into this inventory list, so only membership is the contract.
assert 'widget-006' in (skills.get('synced_skills') or []), skills
assert skills.get('gws_installed') is True, skills
assert not os.path.exists(os.path.join(home, 'knowledge', '.sync_meta.json'))

cache = json.load(open(os.path.join(
    home, 'cache', 'provider_presets.json')))
assert set(cache) == {'presets', 'models'}, cache

stores = db.execute('SELECT COUNT(*) FROM stores').fetchone()[0]
assert stores >= 1, 'the store disappeared in the upgrade'
print('   sync_state imported, legacy keys gone, inventory kept,',
      stores, 'store(s) intact')
PY
[ $? -eq 0 ] || fail "migration assertions failed"

echo "== 5. booting twice is a no-op"
stop_instance
sleep 2
"$REPO/start.sh" $PORT >/dev/null 2>&1
sleep 12
"$PY" - <<PY
import os, sqlite3
db = sqlite3.connect(os.path.join(
    os.environ['VIBE_HOME'], 'data', 'vibe_seller.db'))
row = db.execute(
    "SELECT last_ok_at, version FROM sync_state WHERE job_name='skills'"
).fetchone()
assert row == ('2026-11-01T02:03:04+00:00', 'a' * 40), row
print('   idempotent:', row)
PY
[ $? -eq 0 ] || fail "re-boot changed the imported state"
stop_instance

echo "PASS: upgrade keeps the schedule state and everything else"
