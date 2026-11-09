#!/usr/bin/env bash
# Upgrade test: `vibe-seller upgrade` must leave the running server on
# the NEW version — not just new files on disk under an old process.
#
#   1. build this checkout twice, as 99.0.1 and 99.0.2 (higher than any
#      real release, so PyPI never wins the resolution)
#   2. `uv tool install` 99.0.1 into an isolated tool dir — the
#      install.sh layout — and `vibe-seller start` it on an isolated home
#   3. run the real `vibe-seller upgrade` with 99.0.2 offered via
#      UV_FIND_LINKS (the CLI's own `uv tool upgrade vibe-seller`)
#   4. assert the server on the port booted again and now reports
#      99.0.2, with no manual start in between, and that the upgrade
#      command said so before returning
#   5. `vibe-seller stop` still stops it (the restart kept its pid file)
#
# Both builds come from the same checkout, so the upgrade runs the
# checkout's own `vibe-seller upgrade`: to see this fail, build the
# parent commit (that is the negative control).
#
# The sandbox is created HERE and only the sandbox is ever deleted. An
# exported VIBE_HOME is ignored on purpose: this script starts and stops
# servers, and must never do that to somebody's real install.
#
# Usage: tests/releases/test_cli_upgrade_restart.sh
#   PORT   default: 7824
set -u

PORT=${PORT:-7824}
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
OLD_V=99.0.1
NEW_V=99.0.2
fail() { echo "FAIL: $*" >&2; exit 1; }

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

echo "== 1. build $OLD_V and $NEW_V from this checkout"
# No SPA staged into app/static: /api/version is all this test reads.
for v in $OLD_V $NEW_V; do
  mkdir -p "$WORK/dist-$v"
  (cd "$REPO" && SETUPTOOLS_SCM_PRETEND_VERSION=$v \
    uv build --wheel -q -o "$WORK/dist-$v") \
    || fail "could not build $v"
done

echo "== 2. install $OLD_V (uv tool, as install.sh does) and start it"
UV_FIND_LINKS="$WORK/dist-$OLD_V" uv tool install -q vibe-seller \
  || fail "could not install $OLD_V"
[ "$(vibe-seller --version | awk '{print $NF}')" = "$OLD_V" ] \
  || fail "installed CLI is not $OLD_V"
vibe-seller start --port "$PORT" || fail "could not start $OLD_V"
wait_for_version "$OLD_V" || fail "$OLD_V never came up on port $PORT"
BOOTS_BEFORE=$(boots)
[ "$BOOTS_BEFORE" -ge 1 ] || fail "no boot recorded in $LOG"
echo "   port $PORT serves $OLD_V (boots: $BOOTS_BEFORE)"

echo "== 3. vibe-seller upgrade (offered $NEW_V)"
UV_FIND_LINKS="$WORK/dist-$NEW_V" vibe-seller upgrade 2>&1 \
  | tee "$WORK/upgrade.out"
[ "${PIPESTATUS[0]}" = 0 ] || fail "vibe-seller upgrade exited non-zero"
# The user is told the server is on the new version — the command does
# not return while the old code is still serving.
grep -q "now running $NEW_V" "$WORK/upgrade.out" \
  || fail "vibe-seller upgrade returned without the server on $NEW_V"
[ "$(vibe-seller --version | awk '{print $NF}')" = "$NEW_V" ] \
  || fail "the package on disk is not $NEW_V after the upgrade"

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

echo "== 5. vibe-seller stop still stops it"
vibe-seller stop --port "$PORT" || fail "vibe-seller stop failed"
for _ in $(seq 1 15); do [ -z "$(version_on_port)" ] && break; sleep 1; done
[ -z "$(version_on_port)" ] \
  || fail "port $PORT still serving after vibe-seller stop"
echo "PASS: the upgrade restarted the server onto $NEW_V"
