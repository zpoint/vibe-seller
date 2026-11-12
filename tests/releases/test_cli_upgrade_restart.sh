#!/usr/bin/env bash
# Upgrade test: `vibe-seller upgrade` must leave the running server on
# the NEW version — not just new files on disk under an old process.
#
#   1. build this checkout as 99.0.2 (higher than any real release, so
#      PyPI never wins the resolution) and, with OLD_FROM=checkout (the
#      default), as 99.0.1 too
#   2. `uv tool install` the OLD version into an isolated tool dir — the
#      install.sh layout — `vibe-seller start` it on an isolated home,
#      and create a store
#   3. run the real `vibe-seller upgrade` with 99.0.2 offered via
#      UV_FIND_LINKS (the CLI's own `uv tool upgrade vibe-seller`)
#   4. assert the server on the port booted again and now reports
#      99.0.2, with no manual start in between, and that the upgrade
#      command said so before returning
#   5. the store created on OLD is still there
#   6. `vibe-seller stop` still stops it (the restart kept its pid file)
#
# OLD_FROM=checkout: both builds come from the same checkout, so the
# upgrade runs the checkout's own `vibe-seller upgrade`: to see this
# fail, build the parent commit (that is the negative control).
#
# OLD_FROM=release: OLD is the latest stable release from PyPI,
# installed the way install.sh does (`uv tool install vibe-seller`) —
# the upgrade users actually run, with the OLD release's own CLI. A
# release older than the server's self-restart (#224) cannot restart
# itself: there the user stops and starts it once, and the test does
# exactly that before checking the server.
#
# The sandbox is created HERE and only the sandbox is ever deleted. An
# exported VIBE_HOME is ignored on purpose: this script starts and stops
# servers, and must never do that to somebody's real install.
#
# Usage: tests/releases/test_cli_upgrade_restart.sh
#   PORT      default: 7824
#   OLD_FROM  checkout (default) | release
set -u

PORT=${PORT:-7824}
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
OLD_FROM=${OLD_FROM:-checkout}
NEW_V=99.0.2
fail() {
  echo "FAIL: $*" >&2
  # The sandbox (and its server log) is deleted on exit: show it now.
  if [ -f "${LOG:-}" ]; then echo "--- tail $LOG" >&2; tail -40 "$LOG" >&2; fi
  exit 1
}
case "$OLD_FROM" in
  checkout) OLD_V=99.0.1 ;;
  release)
    # PyPI's own "latest stable": skips yanked releases and nightlies.
    OLD_V=$(curl -fsS https://pypi.org/pypi/vibe-seller/json \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])') \
      || fail "could not read the latest release from PyPI"
    ;;
  *) fail "OLD_FROM must be checkout or release, not '$OLD_FROM'" ;;
esac

WORK=$(mktemp -d "${TMPDIR:-/tmp}/vibe-upgrade-restart.XXXXXX") \
  || fail "could not create the sandbox"
MARKER="$WORK/.owned-by-test_cli_upgrade_restart"
touch "$MARKER"

export VIBE_HOME="$WORK/home"
export UV_TOOL_DIR="$WORK/tools"
export UV_TOOL_BIN_DIR="$WORK/bin"
export PATH="$UV_TOOL_BIN_DIR:$PATH"
export VIBE_SYNC_DISABLED=1   # what a pre-0.2.x client reads
export VIBE_GITHUB_MAIN_SYNC=off
export VIBE_EXTERNAL_SYNC=off   # the current build has two switches
export VIBE_SELLER_TELEMETRY=0
mkdir -p "$VIBE_HOME"

version_on_port() {
  # --noproxy '*': a shell-level proxy must not intercept loopback.
  curl -fsS --noproxy '*' --max-time 3 \
    "http://127.0.0.1:$PORT/api/version" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' \
      2>/dev/null
}

LOG="$VIBE_HOME/logs/backend_$PORT.log"
boots() {
  # uvicorn logs this once per boot to the daemon log, which a restart
  # in place inherits. Independent of anything the app reports.
  grep -c 'Application startup complete' "$LOG" 2>/dev/null || echo 0
}

wait_for_version() {
  local want=$1 got i
  for i in $(seq 1 90); do
    got=$(version_on_port)
    [ "$got" = "$want" ] && return 0
    sleep 1
  done
  echo "   port $PORT reports: ${got:-<nothing>}" >&2
  return 1
}

cleanup() {
  # Stop only what this sandbox started: the CLI's own pid files.
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
    "${TMPDIR:-/tmp}"/vibe-upgrade-restart.*) ;;
    *) echo "refusing to remove '$WORK'" >&2; return 1 ;;
  esac
  # A just-stopped server may still be flushing its log: retry once.
  [ -f "$MARKER" ] && { rm -rf "$WORK" 2>/dev/null || { sleep 2; rm -rf "$WORK"; }; }
}
trap cleanup EXIT

if [ -n "$(version_on_port)" ]; then
  fail "port $PORT is already serving — pick another with PORT=<n>"
fi

echo "== 1. build from this checkout (OLD = $OLD_V from $OLD_FROM, NEW = $NEW_V)"
# No SPA staged into app/static: the API is all this test reads.
BUILD="$NEW_V"
[ "$OLD_FROM" = checkout ] && BUILD="$OLD_V $NEW_V"
for v in $BUILD; do
  mkdir -p "$WORK/dist-$v"
  (cd "$REPO" && SETUPTOOLS_SCM_PRETEND_VERSION=$v \
    uv build --wheel -q -o "$WORK/dist-$v") \
    || fail "could not build $v"
done

echo "== 2. install $OLD_V (uv tool, as install.sh does) and start it"
if [ "$OLD_FROM" = checkout ]; then
  UV_FIND_LINKS="$WORK/dist-$OLD_V" uv tool install -q vibe-seller \
    || fail "could not install $OLD_V"
else
  # Unpinned, from PyPI, as install.sh does (a pin would also stick to
  # `uv tool upgrade`) — and resolved as of the release's upload time,
  # as its users installed it: today's dependency releases can break an
  # old one (0.2.0 relied on browser-use pulling in aiohttp, which later
  # browser-use releases dropped).
  UPLOADED=$(curl -fsS "https://pypi.org/pypi/vibe-seller/$OLD_V/json" \
    | python3 -c 'import json,sys; print(max(u["upload_time_iso_8601"] for u in json.load(sys.stdin)["urls"]))') \
    || fail "could not read $OLD_V's upload time from PyPI"
  uv tool install -q --exclude-newer "$UPLOADED" vibe-seller \
    || fail "could not install $OLD_V from PyPI"
  # uv records the cutoff in the tool's receipt and would apply it to
  # every later `uv tool upgrade`; a user's receipt has none.
  RECEIPT="$UV_TOOL_DIR/vibe-seller/uv-receipt.toml"
  sed -i.bak '/^exclude-newer = /d' "$RECEIPT" && rm -f "$RECEIPT.bak" \
    || fail "could not drop the install cutoff from $RECEIPT"
fi
[ "$(vibe-seller --version | awk '{print $NF}')" = "$OLD_V" ] \
  || fail "installed CLI is not $OLD_V"
vibe-seller start --port "$PORT" || fail "could not start $OLD_V"
wait_for_version "$OLD_V" || fail "$OLD_V never came up on port $PORT"
BOOTS_BEFORE=$(boots)
[ "$BOOTS_BEFORE" -ge 1 ] || fail "no boot recorded in $LOG"
echo "   port $PORT serves $OLD_V (boots: $BOOTS_BEFORE)"
STORE_ID=$(curl -fsS --noproxy '*' -X POST "http://127.0.0.1:$PORT/api/stores" \
  -H 'Content-Type: application/json' \
  -d '{"name":"ci-upgrade-store","browser_backend":"chrome","platforms":["amazon"],"countries":["US"]}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])') \
  || fail "could not create a store on $OLD_V"
# Whether OLD's server restarts itself onto an upgraded install (#224).
# (A file check: `python -c 'import app...'` from the repo root would
# import the checkout's app/, not the installed one.)
SELF_RESTART=0
compgen -G "$UV_TOOL_DIR/vibe-seller/lib/python*/site-packages/app/upgrade_restart.py" \
  >/dev/null && SELF_RESTART=1

echo "== 3. vibe-seller upgrade (offered $NEW_V)"
UV_FIND_LINKS="$WORK/dist-$NEW_V" vibe-seller upgrade 2>&1 \
  | tee "$WORK/upgrade.out"
[ "${PIPESTATUS[0]}" = 0 ] || fail "vibe-seller upgrade exited non-zero"
[ "$(vibe-seller --version | awk '{print $NF}')" = "$NEW_V" ] \
  || fail "the package on disk is not $NEW_V after the upgrade"
if [ "$SELF_RESTART" = 1 ]; then
  # The user is told the server is on the new version — the command
  # does not return while the old code is still serving.
  grep -q "now running $NEW_V" "$WORK/upgrade.out" \
    || fail "vibe-seller upgrade returned without the server on $NEW_V"
else
  echo "   $OLD_V predates the self-restart: stop + start once, as its users do"
  vibe-seller stop --port "$PORT" || fail "could not stop $OLD_V"
  vibe-seller start --port "$PORT" || fail "could not start $NEW_V"
fi

echo "== 4. the running server is the new version"
# Two checks, because /api/version alone was once fooled: it re-read
# the metadata on disk per request, so an un-restarted server reported
# the new version. A second boot in the daemon log cannot be faked.
wait_for_version "$NEW_V" \
  || fail "port $PORT still is not serving $NEW_V — the upgrade left \
the old server running"
BOOTS_AFTER=$(boots)
[ "$BOOTS_AFTER" -gt "$BOOTS_BEFORE" ] \
  || fail "the server never booted again ($BOOTS_BEFORE boot(s) before \
the upgrade, $BOOTS_AFTER after) — the old process is still serving"

echo "== 5. the store created on $OLD_V survived"
curl -fsS --noproxy '*' "http://127.0.0.1:$PORT/api/stores" | grep -q "$STORE_ID" \
  || fail "store $STORE_ID created on $OLD_V is gone after the upgrade"

echo "== 6. vibe-seller stop still stops it"
vibe-seller stop --port "$PORT" || fail "vibe-seller stop failed"
for _ in $(seq 1 15); do [ -z "$(version_on_port)" ] && break; sleep 1; done
[ -z "$(version_on_port)" ] \
  || fail "port $PORT still serving after vibe-seller stop"
echo "PASS: $OLD_V ($OLD_FROM) upgraded; the server runs $NEW_V"
