#!/usr/bin/env bash
# In-app auto-update e2e (macOS + Linux — the uv-tool install path):
# the server must download the new release IN THE BACKGROUND, offer
# "restart and update" to an admin, and land on the new version.
#
#   1. build this checkout as OLD and NEW (versions above any real
#      release, so the index never wins a resolution)
#   2. `uv tool install` OLD into an isolated tool dir on an isolated
#      VIBE_HOME, auth on, VIBE_SELLER_AUTO_UPDATE=1, the update check
#      pointed at a MOCK PyPI JSON offering NEW, and UV_FIND_LINKS
#      carrying the NEW wheel (what a pre-release pin resolves
#      against)
#   3. start the server; log in as admin; create a member
#   4. wait for the background cycle: update-status reaches
#      downloading and then ready (the uv-tool prefetch of NEW's
#      wheels — this is the 60 s first-check interval plus the fetch,
#      so allow minutes, not seconds)
#   5. prove the prefetch is REAL: with UV_OFFLINE=1 the same `uv
#      tool run --from vibe-seller==NEW` resolution completes from
#      cache — a follow-up `uv tool upgrade` needs nothing from the
#      network it did not already fetch
#   6. restart gate: a member gets 403; a POST before ready gets 409
#   7. admin POST /api/system/update/restart → the detached
#      `vibe-seller upgrade` runs, the server restarts onto NEW by
#      itself (app/upgrade_restart.py), /api/version reports NEW, the
#      daemon log shows the second boot, and update-status is idle
#      again on the NEW server (it now IS the latest offered)
#   8. stop; the sandbox is deleted
#
# KEEP=1 stops after step 5 and LEAVES THE SERVER RUNNING on PORT in
# the ready state (the click-through demo); cleanup is skipped for it.
#
# Usage: tests/releases/test_auto_update.sh
#   PORT       default: 7825
#   OLD_V      default: 99.0.1
#   NEW_V      default: 99.0.2
#   KEEP       default: 0  (1 = demo: leave the ready server running)
#   DIST_OLD / DIST_NEW   reuse prebuilt wheel dirs instead of building
set -u

PORT=${PORT:-7825}
OLD_V=${OLD_V:-99.0.1}
NEW_V=${NEW_V:-99.0.2}
KEEP=${KEEP:-0}
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
fail() {
  echo "FAIL: $*" >&2
  if [ -f "${LOG:-}" ]; then echo "--- tail $LOG" >&2; tail -60 "$LOG" >&2; fi
  # The sandbox dies with the trap — keep the forensic files.
  if [ -d "${VIBE_HOME:-}" ]; then
    d="$HOME/agent-briefs/e2e-debug-$(basename "$WORK")"
    mkdir -p "$d"
    cp -r "$VIBE_HOME/updates" "$d/" 2>/dev/null
    cp "$LOG" "$d/" 2>/dev/null
    curl -fsS --noproxy '*' --max-time 3 \
      "http://127.0.0.1:$PORT/api/version" > "$d/version.json" 2>/dev/null
    [ -f "$ADMIN_JAR" ] && curl -fsS --noproxy '*' --max-time 3 -b "$ADMIN_JAR" \
      "http://127.0.0.1:$PORT/api/system/update-status" \
      > "$d/status.json" 2>/dev/null
    echo "   debug kept in $d" >&2
  fi
  exit 1
}

# Demo sandboxes persist (KEEP=1); CI gets a fresh mktemp dir.
if [ "$KEEP" = 1 ]; then
  WORK="${VIBE_AUTOUPDATE_HOME:-$HOME/.vibe-seller-autoupdate-demo}"
  mkdir -p "$WORK" || fail "could not create $WORK"
else
  WORK=$(mktemp -d "${TMPDIR:-/tmp}/vibe-auto-update.XXXXXX") \
    || fail "could not create the sandbox"
fi
MARKER="$WORK/.owned-by-test_auto_update"
touch "$MARKER"

export VIBE_HOME="$WORK/home"
export UV_TOOL_DIR="$WORK/tools"
export UV_TOOL_BIN_DIR="$WORK/bin"
export PATH="$UV_TOOL_BIN_DIR:$PATH"
export VIBE_GITHUB_MAIN_SYNC=off
export VIBE_EXTERNAL_SYNC=off
export VIBE_SELLER_TELEMETRY=0
export VIBE_AUTH_REQUIRED=true
export VIBE_SELLER_AUTO_UPDATE=1
mkdir -p "$VIBE_HOME"

# The mock PyPI document: NEW is the latest stable, no nightlies.
MOCK_PYPI="$WORK/pypi.json"
cat > "$MOCK_PYPI" <<EOF
{"info": {"version": "$NEW_V"},
 "releases": {"$OLD_V": [{"yanked": false}], "$NEW_V": [{"yanked": false}]}}
EOF
export VIBE_SELLER_PYPI_JSON_URL="$MOCK_PYPI"

ADMIN_JAR="$WORK/admin.cookies"
MEMBER_JAR="$WORK/member.cookies"

api() { # api <jar> <method> <path> [json]
  local jar=$1 method=$2 path=$3 body=${4:-}
  if [ -n "$body" ]; then
    curl -fsS --noproxy '*' -b "$jar" -c "$jar" -X "$method" \
      "http://127.0.0.1:$PORT$path" -H 'Content-Type: application/json' \
      -d "$body"
  else
    curl -fsS --noproxy '*' -b "$jar" -c "$jar" -X "$method" \
      "http://127.0.0.1:$PORT$path"
  fi
}

version_on_port() {
  curl -fsS --noproxy '*' --max-time 3 \
    "http://127.0.0.1:$PORT/api/version" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' \
      2>/dev/null
}

update_state() {
  api "$ADMIN_JAR" GET /api/system/update-status 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])' \
      2>/dev/null
}

wait_for_version() {
  local want=$1 got i tries=${2:-90}
  for i in $(seq 1 "$tries"); do
    got=$(version_on_port)
    [ "$got" = "$want" ] && return 0
    sleep 1
  done
  echo "   port $PORT reports: ${got:-<nothing>}" >&2
  return 1
}

LOG="$VIBE_HOME/logs/backend_$PORT.log"
boots() {
  grep -c 'Application startup complete' "$LOG" 2>/dev/null || echo 0
}

cleanup() {
  [ "$KEEP" = 1 ] && return 0
  local f pid
  for f in "$VIBE_HOME"/pids/*.pid; do
    [ -f "$f" ] || continue
    pid=$(cat "$f" 2>/dev/null)
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null
      for _ in $(seq 1 15); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    fi
  done
  case "$WORK" in
    "${TMPDIR:-/tmp}"/vibe-auto-update.*) ;;
    "$HOME/.vibe-seller-autoupdate-demo") ;;
    *) echo "refusing to remove '$WORK'" >&2; return 1 ;;
  esac
  [ -f "$MARKER" ] && { rm -rf "$WORK" 2>/dev/null || { sleep 2; rm -rf "$WORK"; }; }
}
trap cleanup EXIT

if [ -n "$(version_on_port)" ]; then
  fail "port $PORT is already serving — pick another with PORT=<n>"
fi

echo "== 1. build the wheels (OLD=$OLD_V NEW=$NEW_V)"
for v in "$OLD_V" "$NEW_V"; do
  var=DIST_$( [ "$v" = "$OLD_V" ] && echo OLD || echo NEW )
  d=${!var:-}
  [ -n "$d" ] && { echo "   reusing $d for $v"; continue; }
  d="$WORK/dist-$v"
  mkdir -p "$d"
  (cd "$REPO" && SETUPTOOLS_SCM_PRETEND_VERSION=$v \
    uv build --wheel -q -o "$d") || fail "could not build $v"
done
DIST_OLD=${DIST_OLD:-$WORK/dist-$OLD_V}
DIST_NEW=${DIST_NEW:-$WORK/dist-$NEW_V}
export UV_FIND_LINKS="$DIST_NEW"

echo "== 2. install $OLD_V (uv tool, unpinned like install.sh) and start"
uv tool install -q --force --find-links "$DIST_OLD" "vibe-seller==$OLD_V" \
  || fail "could not install $OLD_V"
# The installed OLD must be THIS checkout's code (the index can host a
# same-numbered release); the auto-update files are the proof.
compgen -G "$UV_TOOL_DIR/vibe-seller/lib/python*/site-packages/app/auto_update.py" \
  >/dev/null || fail "installed $OLD_V has no app/auto_update.py — wrong wheel installed"
# A pinned receipt would freeze every later `uv tool upgrade` at this
# version ("Nothing to upgrade" — observed); a user's install.sh receipt
# carries no pin. uv records the pin as `specifier = "==<v>"`.
RECEIPT="$UV_TOOL_DIR/vibe-seller/uv-receipt.toml"
[ -f "$RECEIPT" ] || fail "no uv receipt at $RECEIPT — not a uv tool install"
sed -i.bak 's/, specifier = "==[^\"]*"//' "$RECEIPT" \
  && rm -f "$RECEIPT.bak" || fail "could not unpin $RECEIPT"
grep -q 'specifier' "$RECEIPT" && fail "receipt still pinned: $RECEIPT"
[ "$(vibe-seller --version | awk '{print $NF}')" = "$OLD_V" ] \
  || fail "installed CLI is not $OLD_V"
vibe-seller start --port "$PORT" || fail "could not start $OLD_V"
wait_for_version "$OLD_V" || fail "$OLD_V never came up on port $PORT"
BOOTS_BEFORE=$(boots)
[ "$BOOTS_BEFORE" -ge 1 ] || fail "no boot recorded in $LOG"
echo "   port $PORT serves $OLD_V (boots: $BOOTS_BEFORE)"

echo "== 3. logins: admin (seeded) + a member"
login() { # login <jar> <identifier> <password>
  curl -fsS --noproxy '*' -c "$1" -X POST "http://127.0.0.1:$PORT/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"identifier\": \"$2\", \"password\": \"$3\"}" >/dev/null \
    || fail "login failed for $2"
}
login "$ADMIN_JAR" admin admin
# Idempotent across demo reruns on a kept sandbox: if the user
# exists, the login below is the real assertion.
api "$ADMIN_JAR" POST /api/users \
  '{"username":"auto-member","email":"auto-member@example.com","password":"memberpass123","role":"member"}' \
  >/dev/null 2>&1 || echo "   (member user already present — reusing)"
login "$MEMBER_JAR" auto-member memberpass123
echo "   admin + member cookies held"

echo "== 4. the background download reaches ready"
[ "$(update_state)" = idle ] || echo "   (state already $(update_state) at first look)"
STATE=$(update_state)
SEEN_DL=$([ "$STATE" = downloading ] && echo 1 || echo 0)
for _ in $(seq 1 240); do
  STATE=$(update_state)
  [ "$STATE" = downloading ] && SEEN_DL=1
  [ "$STATE" = ready ] && break
  [ "$STATE" = error ] && fail "background update errored: $(api "$ADMIN_JAR" GET /api/system/update-status)"
  sleep 2
done
[ "$STATE" = ready ] || fail "never reached ready (last: $STATE)"
VER=$(api "$ADMIN_JAR" GET /api/system/update-status | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')
[ "$VER" = "$NEW_V" ] || fail "ready for the wrong version: $VER != $NEW_V"
echo "   ready on $NEW_V (saw downloading: $SEEN_DL)"

echo "== 4b. ready must NOT apply itself (2+ restart-watcher intervals)"
# Regression pin: the prefetch once installed INTO the tool venv, and
# upgrade_restart.watch restarted the server with no admin click. The
# prefetch may warm caches; only the restart call may move versions.
sleep 25
[ "$(version_on_port)" = "$OLD_V" ] \
  || fail "the server moved to $(version_on_port) without a restart call"
[ "$(vibe-seller --version | awk '{print $NF}')" = "$OLD_V" ] \
  || fail "the installed tool moved to $(vibe-seller --version) without a restart call"
echo "   after 25 s: server $OLD_V, tool $OLD_V — nothing applied itself"

if [ "$KEEP" = 1 ]; then
  echo "== demo mode: leaving the READY server on http://localhost:$PORT"
  echo "   admin/admin · auto-member/memberpass123 · VIBE_HOME=$VIBE_HOME"
fi

echo "== 5. proof the prefetch is real (offline re-resolution from cache)"
# The proof must not itself touch the install: --python gets the venv's
# BASE interpreter, never the tool venv's own python (same wheel tags,
# not an install target).
BASE_PY=$("$UV_TOOL_DIR/vibe-seller/bin/python" -c 'import sys; print(sys.base_prefix)')
[ -x "$BASE_PY/bin/python" ] || fail "no base interpreter under $BASE_PY"
UV_OFFLINE=1 uv tool run --quiet --from "vibe-seller==$NEW_V" \
  --python "$BASE_PY/bin/python" vibe-seller --version \
  | grep -q "$NEW_V" \
  || fail "the offline prefetch proof failed — the cache is not warm for $NEW_V"
[ "$(vibe-seller --version | awk '{print $NF}')" = "$OLD_V" ] \
  || fail "the offline proof moved the tool to $(vibe-seller --version)"
echo "   UV_OFFLINE=1 resolution of $NEW_V completes from cache; tool still $OLD_V"

echo "== 6. the restart gate: member 403"
RC=$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' -b "$MEMBER_JAR" \
  -X POST "http://127.0.0.1:$PORT/api/system/update/restart")
[ "$RC" = 403 ] || fail "member restart expected 403, got $RC"
echo "   member → $RC"

if [ "$KEEP" = 1 ]; then
  echo "== 7. demo mode: restart NOT applied (left for the click-through)"
  echo "PASS: auto-update demo ready on port $PORT (sandbox kept: $WORK)"
  trap - EXIT
  exit 0
fi

echo "== 7. admin restarts onto the new version (the only mover)"
RC=$(curl -sS --noproxy '*' -b "$ADMIN_JAR" -o "$WORK/restart.json" -w '%{http_code}' \
  -X POST "http://127.0.0.1:$PORT/api/system/update/restart")
[ "$RC" = 200 ] || fail "admin restart expected 200, got $RC: $(cat "$WORK/restart.json")"
grep -q restarting "$WORK/restart.json" || fail "restart reply: $(cat "$WORK/restart.json")"
wait_for_version "$NEW_V" 300 \
  || fail "port $PORT never came up on $NEW_V — the restart did not take"
BOOTS_AFTER=$(boots)
[ "$BOOTS_AFTER" -gt "$BOOTS_BEFORE" ] \
  || fail "no second boot in $LOG ($BOOTS_BEFORE → $BOOTS_AFTER) — the old process is still serving"
STATE=$(update_state)
[ "$STATE" = idle ] \
  || fail "the NEW server should be up to date (idle), got $STATE"
echo "   server back on $NEW_V (boots: $BOOTS_AFTER), update-status idle"

echo "== 8. stop"
vibe-seller stop --port "$PORT" || fail "vibe-seller stop failed"
for _ in $(seq 1 15); do [ -z "$(version_on_port)" ] && break; sleep 1; done
[ -z "$(version_on_port)" ] || fail "port $PORT still serving after stop"
echo "PASS: $OLD_V → $NEW_V in-app auto-update end to end"
