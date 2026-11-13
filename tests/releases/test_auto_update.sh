#!/usr/bin/env bash
# In-app auto-update end to end (macOS + Linux — the uv-tool install).
#
# What this proves is the part no unit test can: that a real installed
# server, running real code out of a real uv tool, fetches a new release
# in the background and puts itself onto it — and, when it cannot, puts
# the OLD version back rather than leaving the machine dark.
#
# Legs (LEG=s1,s5 … or `all`, the default):
#
#   s1  OLD → NEW, nothing running, NO CLICK. The update reaches ready on
#       the pip path and the server restarts onto NEW by itself. Also
#       holds #244's gate: a member gets 403, and a restart before
#       `ready` gets 409.
#   s2  A task is running (the mock CLI, so no provider is called and no
#       money moves). The update must NOT apply while it runs, and must
#       apply on the first tick after it ends.
#   s5  NEW is a fabricated BROKEN release — it imports, then dies on
#       boot. The apply must fail, reinstate OLD offline, and leave OLD
#       serving, with the state `error`. Never a dead server.
#   s5b OLD → NEW (ok) → BROKEN. The rollback must land on NEW — the
#       version that was actually serving — not on OLD. A rollback that
#       only works for the first hop is the bug this leg exists for.
#   s7  A dev build is never offered a version and never applies one.
#
# The fabricated releases are built from THIS checkout with a patched
# source tree (see build_wheel), not from hooks in the product.
#
# Usage: tests/releases/test_auto_update.sh
#   PORT      default 7825; each leg uses PORT + leg index
#   LEG       default `all`
#   OLD_V / NEW_V / BROKEN_V    default 99.0.1 / 99.0.2 / 99.0.3
#   KEEP      1 = leave the leg's server running in READY state (demo)
#   DIST_OLD / DIST_NEW / DIST_BROKEN   reuse prebuilt wheel dirs
set -u

PORT=${PORT:-7825}
OLD_V=${OLD_V:-99.0.1}
NEW_V=${NEW_V:-99.0.2}
BROKEN_V=${BROKEN_V:-99.0.3}
LEG=${LEG:-all}
KEEP=${KEEP:-0}
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
fail() {
  echo "FAIL: $*" >&2
  if [ -f "${LOG:-}" ]; then echo "--- tail $LOG" >&2; tail -60 "$LOG" >&2; fi
  if [ -d "${WORK:-}" ]; then
    d="$HOME/agent-briefs/e2e-debug-$(basename "${WORK}")"
    mkdir -p "$d"
    cp -r "$VIBE_HOME/updates" "$d/" 2>/dev/null
    [ -f "$LOG" ] && cp "$LOG" "$d/" 2>/dev/null
    curl -fsS --noproxy '*' --max-time 3 \
      "http://127.0.0.1:$PORT/api/version" > "$d/version.json" 2>/dev/null
    [ -f "${ADMIN_JAR:-}" ] && curl -fsS --noproxy '*' --max-time 3 \
      -b "$ADMIN_JAR" "http://127.0.0.1:$PORT/api/system/update-status" \
      > "$d/status.json" 2>/dev/null
    echo "   debug kept in $d" >&2
  fi
  exit 1
}

# ── shared wheel set ────────────────────────────────────────────────
# Built once for all legs. A leg that starts from a version another leg
# already built pays nothing for it.
export TMPDIR=${TMPDIR:-/tmp}
BUILT="$TMPDIR/vibe-auto-update-wheels.$$"
mkdir -p "$BUILT"

# build_wheel <version> <out-dir> [break|…]
#
# `break` produces the fabricated bad release: app/main.py gains a line
# that raises when the SERVER imports it. Nothing else in the package
# reads main.py, so the CLI still reports its version and the update
# check still prefetches it — the release is installable and only the
# boot fails, which is the failure an operator actually sees.
build_wheel() {
  local version=$1 out=$2 mode=${3:-ok} src=$REPO
  if [ -d "$out" ] && compgen -G "$out/*.whl" >/dev/null; then
    echo "   reusing $out for $version"
    return 0
  fi
  mkdir -p "$out"
  if [ "$mode" = break ]; then
    src="$BUILT/src-$version"
    rm -rf "$src"
    mkdir -p "$src"
    # The checkout may be a worktree, whose .git is a file pointing at
    # the main repo — useless once copied, and setuptools-scm is told
    # the version anyway.
    tar -C "$REPO" --exclude=.git --exclude=node_modules --exclude=.venv \
      --exclude=frontend -cf - . | tar -C "$src" -xf -
    printf '\nraise SystemExit("fabricated broken release")\n' \
      >> "$src/app/main.py"
  fi
  (cd "$src" && SETUPTOOLS_SCM_PRETEND_VERSION=$version \
    uv build --wheel -q -o "$out") || fail "could not build $version ($mode)"
}

echo "== building the wheel set (OLD=$OLD_V NEW=$NEW_V BROKEN=$BROKEN_V)"
DIST_OLD=${DIST_OLD:-$BUILT/dist-$OLD_V}
DIST_NEW=${DIST_NEW:-$BUILT/dist-$NEW_V}
DIST_BROKEN=${DIST_BROKEN:-$BUILT/dist-$BROKEN_V}
build_wheel "$OLD_V" "$DIST_OLD"
build_wheel "$NEW_V" "$DIST_NEW"
build_wheel "$BROKEN_V" "$DIST_BROKEN" break
# The broken wheel must be a real wheel of the right version, or s5
# would prove nothing about a broken release.
compgen -G "$DIST_BROKEN/vibe_seller-$BROKEN_V-*.whl" >/dev/null \
  || fail "no $BROKEN_V wheel built"

# ── the leg runner ─────────────────────────────────────────────────
run_leg() { # run_leg <leg> <target-version> <dist-dir> [extra env…]
  local leg=$1 target=$2 dist=$3
  PORT=$((BASE_PORT + LEG_INDEX)); LEG_INDEX=$((LEG_INDEX + 1))
  echo ""
  echo "################ LEG $leg (port $PORT, target $target)"
  WORK=$(mktemp -d "${TMPDIR:-/tmp}/vibe-au-$leg.XXXXXX") || fail "mktemp"
  local marker="$WORK/.owned-by-test_auto_update"
  touch "$marker"
  export VIBE_HOME="$WORK/home"
  export UV_TOOL_DIR="$WORK/tools"
  export UV_TOOL_BIN_DIR="$WORK/bin"
  export PATH="$UV_TOOL_BIN_DIR:$PATH"
  export VIBE_GITHUB_MAIN_SYNC=off VIBE_EXTERNAL_SYNC=off
  export VIBE_SELLER_TELEMETRY=0 VIBE_AUTH_REQUIRED=true
  export VIBE_SELLER_AUTO_UPDATE=1
  mkdir -p "$VIBE_HOME"

  # The mock PyPI document: the target is the newest stable release.
  local mock="$WORK/pypi.json"
  cat > "$mock" <<EOF
{"info": {"version": "$target"},
 "releases": {"$OLD_V": [{"yanked": false}], "$target": [{"yanked": false}]}}
EOF
  export VIBE_SELLER_PYPI_JSON_URL="$mock"
  export UV_FIND_LINKS="$DIST_OLD,$dist,$DIST_BROKEN"

  ADMIN_JAR="$WORK/admin.cookies"; MEMBER_JAR="$WORK/member.cookies"
  LOG="$VIBE_HOME/logs/backend_$PORT.log"

  if [ -n "$(version_on_port)" ]; then
    fail "port $PORT is already serving — pick another with PORT=<n>"
  fi

  echo "-- install $OLD_V and start"
  uv tool install -q --force --find-links "$DIST_OLD" "vibe-seller==$OLD_V" \
    || fail "could not install $OLD_V"
  compgen -G "$UV_TOOL_DIR/vibe-seller/lib/python*/site-packages/app/auto_update.py" \
    >/dev/null || fail "installed $OLD_V is not this checkout's code"
  # An unpinned receipt, like install.sh leaves: a pin would freeze every
  # later `uv tool upgrade` at this version.
  local receipt="$UV_TOOL_DIR/vibe-seller/uv-receipt.toml"
  [ -f "$receipt" ] || fail "no uv receipt — not a uv tool install"
  sed -i.bak 's/, specifier = "==[^"]*"//' "$receipt" && rm -f "$receipt.bak" \
    || fail "could not unpin the receipt"
  grep -q 'specifier' "$receipt" && fail "receipt still pinned"
  [ "$(vibe-seller --version | awk '{print $NF}')" = "$OLD_V" ] \
    || fail "installed CLI is not $OLD_V"

  # Every leg needs a session: /api/system/update-status is behind
  # auth, and a leg that cannot read it proves nothing.
  echo "-- logins"
  login "$ADMIN_JAR" admin admin
  api "$ADMIN_JAR" POST /api/users \
    '{"username":"auto-member","email":"a-m@example.com","password":"memberpass123","role":"member"}' \
    >/dev/null 2>&1 || echo "   (member exists — reusing)"
  login "$MEMBER_JAR" auto-member memberpass123

  if [ "$leg" = s7 ]; then
    # A dev-shaped version: setuptools-scm's own shape, not a nightly.
    echo "-- s7 reinstalls as a DEV build and expects to be left alone"
    local devdist="$WORK/dist-dev"
    build_dev_wheel "$devdist"
    UV_FIND_LINKS="$devdist" uv tool install -q --force \
      --find-links "$devdist" "vibe-seller==$DEV_V" \
      || fail "could not install the dev build"
    sed -i.bak 's/, specifier = "==[^"]*"//' "$receipt" && rm -f "$receipt.bak"
    [ "$(vibe-seller --version | awk '{print $NF}')" = "$DEV_V" ] \
      || fail "installed CLI is not the dev version"
    vibe-seller start --port "$PORT" || fail "dev build would not start"
    wait_for_version "$DEV_V" || fail "dev build never came up"
    assert_idle_no_update "a dev build"
    stop_leg
    return 0
  fi

  vibe-seller start --port "$PORT" || fail "could not start $OLD_V"
  wait_for_version "$OLD_V" || fail "$OLD_V never came up on $PORT"
  BOOTS_BEFORE=$(boots); [ "$BOOTS_BEFORE" -ge 1 ] || fail "no boot logged"
  echo "   port $PORT serves $OLD_V (boots: $BOOTS_BEFORE)"

  # #244's gate, asserted before `ready` exists: 409, and 403 for a
  # member once it does.
  local rc
  rc=$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' -b "$ADMIN_JAR" \
    -X POST "http://127.0.0.1:$PORT/api/system/update/restart")
  [ "$rc" = 409 ] || fail "restart before ready expected 409, got $rc"

  echo "-- wait for the background cycle to reach ready"
  local state='' saw_dl=0
  for _ in $(seq 1 200); do
    state=$(update_field state)
    [ "$state" = downloading ] && saw_dl=1
    [ "$state" = ready ] && break
    [ "$state" = error ] && fail "the background update errored: $(status_json)"
    sleep 2
  done
  [ "$state" = ready ] || fail "never reached ready (last: $state)"
  echo "   ready (saw downloading: $saw_dl)"
  local ready_version path auto
  ready_version=$(update_field version)
  path=$(update_field path)
  auto=$(update_field auto_apply)
  [ "$ready_version" = "$target" ] \
    || fail "ready for the wrong version: $ready_version != $target"
  # macOS/Linux have no installer to fingerprint: this must be the pip
  # path, and it must be the path that applies itself.
  [ "$path" = pip ] || fail "expected the pip path, got '$path'"
  [ "$auto" = true ] || fail "a pip update must report auto_apply=true"

  rc=$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' -b "$MEMBER_JAR" \
    -X POST "http://127.0.0.1:$PORT/api/system/update/restart")
  [ "$rc" = 403 ] || fail "member restart expected 403, got $rc"
  echo "   gate: member 403, pre-ready 409"

  # The wheels must already be in the cache: proof is the offline
  # resolution of the target, which is what `ready` promised. Done with
  # the venv's BASE interpreter, never the tool venv's own python.
  local base_py
  base_py=$("$UV_TOOL_DIR/vibe-seller/bin/python" -c 'import sys; print(sys.base_prefix)')
  UV_OFFLINE=1 uv tool run --quiet --from "vibe-seller==$target" \
    --python "$base_py/bin/python" vibe-seller --version \
    | grep -q "$target" || fail "the cache is not warm for $target"
  [ "$(vibe-seller --version | awk '{print $NF}')" = "$OLD_V" ] \
    || fail "the offline proof itself moved the tool"
  echo "   proof: $target resolves offline from the cache; tool still $OLD_V"

  case $leg in
    s1)
      echo "-- S1: no click. Wait for the server to move by itself."
      wait_for_version "$target" 200 \
        || fail "the idle apply never happened (port still $(version_on_port))"
      [ "$(boots)" -gt "$BOOTS_BEFORE" ] || fail "no second boot in $LOG"
      # The server that restarted is the NEW one; it must say so.
      [ "$(update_field state)" = idle ] \
        || fail "the NEW server is not idle: $(status_json)"
      local rec
      rec=$(record_field status)
      [ "$rec" = ok ] || fail "the apply record says '$rec': $(record_json)"
      [ "$(record_field path)" = pip ] || fail "record path != pip"
      [ "$(record_field trigger)" = auto_idle ] \
        || fail "record trigger != auto_idle: $(record_json)"
      echo "   OK: applied itself onto $target, recorded pip/auto_idle/ok"
      ;;
    s2)
      echo "-- S2: occupy the server with a task, and check it waits"
      # The mock CLI answers stream-json with no provider call; MOCK_CLI
      # is the repo's existing hook for that.
      export MOCK_CLI="$REPO/tests/e2e/mock_cli.py"
      export MOCK_CLI_SCENARIO='{"result":"still working","delay":25}'
      local task
      task=$(api "$ADMIN_JAR" POST /api/tasks \
        '{"title":"hold the server","description":"keep running","plan_mode":false}') \
        || fail "could not create the task"
      local tid; tid=$(echo "$task" | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
      local waited=0 live=0
      while [ $waited -lt 60 ]; do
        live=$(api "$ADMIN_JAR" GET /api/system/update-status \
          | python3 -c 'import json,sys;print(json.load(sys.stdin)["running_tasks"])')
        [ "$live" != 0 ] && break
        sleep 1; waited=$((waited + 1))
      done
      [ "$live" != 0 ] || fail "no agent became live; MOCK_CLI=$MOCK_CLI"
      echo "   a task is live (running_tasks=$live)"
      # Busy for a good two idle-check intervals: nothing may move.
      local before=$(version_on_port) held=0
      while [ $held -lt 150 ]; do
        [ "$(version_on_port)" = "$before" ] \
          || fail "the update applied over a running task"
        [ "$(installed_version)" = "$OLD_V" ] \
          || fail "the install moved while a task was running"
        sleep 2; held=$((held + 2))
        # Let the task finish part-way through, then keep watching.
        [ $held -eq 60 ] && echo "   still $before at +60s (correct)"
      done
      echo "   held for 150s with the task running — nothing applied"
      wait_for_task_done "$ADMIN_JAR" "$tid" || fail "the mock task never finished"
      wait_for_version "$target" 200 \
        || fail "after the task ended the update still did not apply"
      echo "   OK: waited for the task, then applied onto $target by itself"
      ;;
    s5|s5b)
      echo "-- $leg: the target will not boot; the machine must come back"
      local expect_from=$OLD_V
      [ "$leg" = s5b ] && expect_from=$NEW_V
      if [ "$leg" = s5b ]; then
        echo "   hop 1: $OLD_V -> $NEW_V (a good release)"
        wait_for_version "$NEW_V" 200 || fail "hop 1 never applied"
        stop_leg
        # Same install tree, new target: the BROKEN wheel. Re-point the
        # mock index and restart, so the update check offers 99.0.3.
        cat > "$mock" <<EOF
{"info": {"version": "$BROKEN_V"},
 "releases": {"$NEW_V": [{"yanked": false}], "$BROKEN_V": [{"yanked": false}]}}
EOF
        unset MOCK_CLI
        vibe-seller start --port "$PORT" || fail "hop 1 restart failed"
        wait_for_version "$NEW_V" || fail "the server did not come back on $NEW_V"
        BOOTS_BEFORE=$(boots)
        echo "   serving $NEW_V; now offering the broken $BROKEN_V"
        target=$BROKEN_V; dist=$DIST_BROKEN
      fi
      state=''
      for _ in $(seq 1 200); do
        state=$(update_field state)
        [ "$state" = ready ] && break
        [ "$state" = error ] && break
        sleep 2
      done
      [ "$state" = ready ] || fail "never reached ready for the broken build ($state)"
      echo "-- it is ready; now it must apply, fail, and put $expect_from back"
      local gone=0
      for _ in $(seq 1 260); do
        v=$(version_on_port)
        if [ "$v" = "$expect_from" ]; then gone=1; break; fi
        sleep 2
      done
      [ "$gone" = 1 ] || fail "the machine never came back on $expect_from (now: $(version_on_port || echo DOWN))"
      health_ok || fail "the server answers /api/version but not /api/health"
      [ "$(installed_version)" = "$expect_from" ] \
        || fail "the INSTALL is still $(installed_version) — no real rollback"
      [ "$(record_field status)" = rolled_back ] \
        || fail "the record should say rolled_back: $(record_json)"
      echo "   OK: the broken release failed, $expect_from is serving again"
      echo "   record: $(record_json)"
      ;;
  esac
  stop_leg
}

# ── small helpers ───────────────────────────────────────────────────
BASE_PORT=$PORT
LEG_INDEX=0
DEV_V=${DEV_V:-99.0.9.dev7+g1234567}

api() { # api <jar> <method> <path> [json]
  local jar=$1 method=$2 path=$3 body=${4:-}
  if [ -n "$body" ]; then
    curl -fsS --noproxy '*' -b "$jar" -c "$jar" -X "$method" \
      "http://127.0.0.1:$PORT$path" -H 'Content-Type: application/json' -d "$body"
  else
    curl -fsS --noproxy '*' -b "$jar" -c "$jar" -X "$method" \
      "http://127.0.0.1:$PORT$path"
  fi
}
login() { # login <jar> <identifier> <password>
  curl -fsS --noproxy '*' -c "$1" -X POST "http://127.0.0.1:$PORT/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"identifier\": \"$2\", \"password\": \"$3\"}" >/dev/null \
    || fail "login failed for $2"
}
json_field() { python3 -c "import json,sys;print(json.load(sys.stdin).get('$1'))" 2>/dev/null; }
version_on_port() {
  curl -fsS --noproxy '*' --max-time 3 "http://127.0.0.1:$PORT/api/version" 2>/dev/null
}
update_field() { api "$ADMIN_JAR" GET /api/system/update-status 2>/dev/null | json_field "$1"; }
status_json() { api "$ADMIN_JAR" GET /api/system/update-status 2>/dev/null; }
record_json() { cat "$VIBE_HOME/updates/last.json" 2>/dev/null; }
record_field() { record_json | json_field "$1"; }
installed_version() { vibe-seller --version 2>/dev/null | awk '{print $NF}'; }
health_ok() {
  curl -fsS --noproxy '*' --max-time 5 "http://127.0.0.1:$PORT/api/health" 2>/dev/null \
    | grep -q '"ok"'
}
boots() { grep -c 'Application startup complete' "$LOG" 2>/dev/null || echo 0; }
wait_for_version() {
  local want=$1 tries=${2:-90} got i
  for i in $(seq 1 "$tries"); do
    got=$(version_on_port | json_field version)
    [ "$got" = "$want" ] && return 0
    sleep 1
  done
  echo "   port $PORT reports: ${got:-<nothing>}" >&2
  return 1
}
wait_for_task_done() { # wait_for_task_done <jar> <task id>
  local jar=$1 tid=$2 st
  for _ in $(seq 1 120); do
    st=$(api "$jar" GET "/api/tasks/$tid" | json_field status)
    case $st in completed|failed) return 0 ;; esac
    sleep 2
  done
  return 1
}
assert_idle_no_update() { # a dev build must be left alone entirely
  local who=$1 st
  for _ in $(seq 1 90); do
    st=$(update_field state)
    [ "$st" = ready ] && fail "$who reached ready — it must never update"
    [ "$st" = downloading ] && fail "$who started a download"
    sleep 2
  done
  [ "$(update_field state)" = idle ] || fail "$(update_field state) for $who"
  [ "$(update_field version)" = None ] || fail "offered $(update_field version)"
  [ "$(update_field supported)" = False ] || fail "a dev build reports supported"
  echo "   OK: $who stayed idle and unsupported for 180s"
}
build_dev_wheel() { # a setuptools-scm dev shape, not a nightly
  local out=$1
  if [ -d "$out" ] && compgen -G "$out/*.whl" >/dev/null; then return 0; fi
  mkdir -p "$out"
  (cd "$REPO" && SETUPTOOLS_SCM_PRETEND_VERSION=$DEV_V \
    uv build --wheel -q -o "$out") || fail "could not build the dev wheel"
}
stop_leg() {
  [ "$KEEP" = 1 ] && { echo "   KEEP=1: leaving $PORT up in $WORK"; return 0; }
  local f pid
  for f in "$VIBE_HOME"/pids/*.pid; do
    [ -f "$f" ] || continue
    pid=$(cat "$f" 2>/dev/null)
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null
      for _ in $(seq 1 15); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    fi
  done
  vibe-seller stop --port "$PORT" 2>/dev/null
  for _ in $(seq 1 15); do [ -z "$(version_on_port)" ] && break; sleep 1; done
  [ -z "$(version_on_port)" ] || fail "port $PORT still serving after stop"
  case "$WORK" in
    "${TMPDIR:-/tmp}"/vibe-au-*) rm -rf "$WORK" ;;
    *) echo "refusing to remove '$WORK'" >&2 ;;
  esac
}

cleanup_all() {
  rm -rf "$BUILT"
  [ -n "${PIDS:-}" ] && kill -- -"$PIDS" 2>/dev/null
  return 0
}
trap cleanup_all EXIT

for one in $(echo "$LEG" | tr ',' ' '); do
  case $one in
    s1) run_leg s1 "$NEW_V" "$DIST_NEW" ;;
    s2) run_leg s2 "$NEW_V" "$DIST_NEW" ;;
    s5) run_leg s5 "$BROKEN_V" "$DIST_BROKEN" ;;
    s5b) run_leg s5b "$BROKEN_V" "$DIST_BROKEN" ;;
    s7) run_leg s7 "$NEW_V" "$DIST_NEW" ;;
    all) run_leg s1 "$NEW_V" "$DIST_NEW"
         run_leg s2 "$NEW_V" "$DIST_NEW"
         run_leg s5 "$BROKEN_V" "$DIST_BROKEN"
         run_leg s5b "$BROKEN_V" "$DIST_BROKEN"
         run_leg s7 "$NEW_V" "$DIST_NEW" ;;
    *) fail "unknown LEG '$one' (s1 s2 s5 s5b s7 all)" ;;
  esac
done

echo ""
echo "PASS: legs ($LEG) — auto-update end to end on $(uname)"
