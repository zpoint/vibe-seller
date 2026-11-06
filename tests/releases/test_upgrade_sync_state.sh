#!/bin/zsh
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
# Usage: tests/releases/test_upgrade_sync_state.sh [previous-version]
#   PREV_VERSION   default: latest stable on PyPI
#   VIBE_HOME      default: $TMPDIR/vibe-upgrade-home
#   PORT           default: 7813
set -u

PREV_VERSION=${1:-}
PORT=${PORT:-7813}
WORK=${WORKDIR:-$TMPDIR/vibe-upgrade-test}
VH=${VIBE_HOME:-$WORK/home}
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
PY=$REPO/.venv/bin/python
FAKE_PORT=${FAKE_PORT:-7814}
fail() { echo "FAIL: $*" >&2; exit 1; }

# Stop ONLY the instance we started. On this machine `stop.sh` also
# kills the user's port clients and reaps every task agent on the box
# (other servers' agents included), so a script must never call it:
# SIGTERM the pid file the starter wrote, then wait for the exit.
stop_instance() {
  local pid f
  for f in "$REPO/.pids_$PORT" "$VH/pids/backend_$PORT.pid"; do
    [ -f "$f" ] || continue
    pid=$(cat "$f" 2>/dev/null)
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

rm -rf "$WORK"
mkdir -p "$WORK" "$VH"

echo "== 1. previous stable in its own venv"
python3 -m venv "$WORK/old" >/dev/null
OLD="$WORK/old/bin"
"$OLD/pip" install -q "vibe-seller${PREV_VERSION:+==$PREV_VERSION}" \
  || fail "could not install the previous release"
OLD_VERSION=$("$OLD/vibe-seller" --version 2>/dev/null | tail -1)
echo "   previous: ${OLD_VERSION:-unknown}"

echo "== 2. boot the old client on an isolated home"
export VIBE_HOME=$VH
export VIBE_SYNC_DISABLED=1
export VIBE_SYNC_JITTER=0
"$OLD/vibe-seller" start --port $PORT >/dev/null 2>&1 &
OLD_PID=$!
sleep 20
curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null \
  || fail "the old client did not come up"
# A store, so the upgrade has real state to keep.
curl -fsS -X POST "http://127.0.0.1:$PORT/api/stores" \
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

echo "== 3. the current build over the same home"
"$PY" -m pip install -q -e "$REPO" || fail "could not install this build"
export VIBE_SYNC_DISABLED=1
"$REPO/start.sh" $PORT >/dev/null 2>&1
sleep 15
curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null \
  || fail "the new server did not come up"

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
assert skills.get('synced_skills') == ['widget-006'], skills
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
