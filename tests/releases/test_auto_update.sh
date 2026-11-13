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
#   s5c S5, then the SAME offer again after a fresh boot: the release
#       this machine already put back must never be applied by itself a
#       second time (review 4 — without that rule every check cycle is
#       another outage). The idle tick refuses, the admin is told, and
#       the record is not rewritten.
#   s7  A dev build is never offered a version and never applies one.
#   s9  (release gate, .github/workflows/pip-upgrade-gate.yml) The
#       STARTING install is the published release from PyPI, not a build
#       of this checkout — i.e. what a customer actually runs — and the
#       candidate is this checkout's wheel. Same expectations as s1:
#       ready on the pip path, applied by itself with no click. It
#       SKIPs, green, when that published release predates the idle
#       apply (`app/update_idle.py` is not in its wheel), because then
#       there is no self-applying update on it to test.
#
# The fabricated releases are built from THIS checkout with a patched
# source tree (see build_wheel), not from hooks in the product.
#
# Usage: tests/releases/test_auto_update.sh
#   PORT      default 7825; each leg uses PORT + leg index
#   LEG       default `all`
#   PREV_V    LEG=s9 only: the published version to start from
#   RUN_STAMP   the version prefix for this run (default: the clock)
#   OLD_V / NEW_V / BROKEN_V / PLAIN_V   99.<stamp>.1 … .4
#   KEEP      1 = leave the leg's server running in READY state (demo)
#   DIST_OLD / DIST_NEW / DIST_BROKEN   reuse prebuilt wheel dirs
set -u

PORT=${PORT:-7825}
# Every run gets its OWN version numbers, stamped from the clock. A leg
# that reused 99.0.1/99.0.2 across runs measured whatever the uv cache
# still held under those names from an earlier build of a DIFFERENT
# tree — measured 2026-11-13: leg s1 installed a 99.0.1 that predated
# the fix under test, and the apply ran the old code. Real releases are
# immutable, so this collision cannot happen in production; here it can,
# silently, and it makes a PASS mean nothing.
# `10#` because the clock can start with a zero, and PEP 440 drops it:
# 99.065644.3 is a legal version whose WHEEL is named 99.65644.3, so
# every `$BROKEN_V`-shaped glob and every `--version` comparison in this
# script would miss it (the first CI run of these legs failed on exactly
# that: "no 99.065644.3 wheel built"). Normalizing here, before anything
# is built, is the one place the rule can live.
RUN_STAMP=${RUN_STAMP-$((10#$(date +%H%M%S)))}
OLD_V=${OLD_V:-99.$RUN_STAMP.1}
NEW_V=${NEW_V:-99.$RUN_STAMP.2}
BROKEN_V=${BROKEN_V:-99.$RUN_STAMP.3}
# A good release NEWER than the one leg s1 auto-applies to, used by the
# receipt check at the end of that leg.
PLAIN_V=${PLAIN_V:-99.$RUN_STAMP.4}
LEG=${LEG:-all}
KEEP=${KEEP:-0}
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
fail() {
  echo "FAIL: $*" >&2
  # A leg that fails says everything about the machine at that moment:
  # which version answers, which one is installed, how many boots the
  # log has, what update-status and last.json say, whether the helper
  # was ever spawned, and what it logged. Diagnosing the last two CI
  # failures needed all of these and they were not in the output.
  if [ -d "${VIBE_HOME:-}" ]; then dump_apply_state; fi
  if [ -f "${LOG:-}" ]; then echo "--- tail $LOG" >&2; tail -60 "$LOG" >&2; fi
  if [ -d "${WORK:-}" ]; then
    # DEBUG_DIR keeps a failed leg's evidence somewhere the driver can
    # read it; the default is the same temp root the leg itself uses, so
    # a shipped test never names a person's home directory.
    d="${DEBUG_DIR:-${TMPDIR:-/tmp}}/vibe-au-debug-$(basename "${WORK}")"
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
  # A failed leg leaves ITS SERVER RUNNING on purpose (the debug above is
  # read from it), and that server is the first thing the next run of
  # this leg will trip over — say who to stop, with the pid, rather than
  # letting the next failure look like a mystery.
  local owner
  owner=$(ss -ltnp 2>/dev/null | grep -w "$PORT" | head -1)
  [ -n "$owner" ] && echo "   NOTE: port $PORT is still occupied: $owner" \
    >&2 && echo "         stop it with: ./stop.sh $PORT --scoped" >&2
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
#
# EVERY wheel — the good ones included — is built from a COPY of the
# checkout, never in it. Not for tidiness: `uv build` writes its own
# metadata into the tree it builds from, and `vibe_seller.egg-info/
# PKG-INFO` then says `Version: 99.152807.2`. That file sits in the
# repo root, which is on `sys.path` for anything run from the checkout,
# so `importlib.metadata.version('vibe-seller')` — which is exactly
# what app/version.py calls — starts answering the fabricated version
# to the TEST SUITE. Measured 2026-11-13: one leg's build made
# `tests/workflow/test_contracts.py::test_release_notes_shape` pass
# locally and fail in CI, because the endpoint under test filters
# releases newer than the version running here, and "the version
# running here" had been rewritten from 0.3.x to 99.x by an unrelated
# test run 40 minutes earlier. A leg that edits the tree it is also
# asserting about is not a leg; a build that dirties the checkout can
# also race a leg that is reading it.
build_wheel() {
  local version=$1 out=$2 mode=${3:-ok}
  if [ -d "$out" ] && compgen -G "$out/*.whl" >/dev/null; then
    echo "   reusing $out for $version"
    return 0
  fi
  mkdir -p "$out"
  local src="$BUILT/src-$version-$mode"
  rm -rf "$src"
  mkdir -p "$src"
  # The checkout may be a worktree, whose .git is a file pointing at
  # the main repo — useless once copied, and setuptools-scm is told
  # the version anyway. The build's own leftovers are excluded so a
  # copy never inherits another run's metadata.
  tar -C "$REPO" --exclude=.git --exclude=node_modules --exclude=.venv \
    --exclude=frontend --exclude=build --exclude='*.egg-info' \
    --exclude=.ruff_cache --exclude=.pytest_cache \
    -cf - . | tar -C "$src" -xf -
  if [ "$mode" = break ]; then
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
# LEG=s9 installs the published release from PyPI, so a local OLD wheel
# would be pointless — and pretending this checkout is a version that
# really exists (0.3.1) is worse than pointless: it would shadow the
# real one in UV_FIND_LINKS. Only the candidate is built for it.
DIST_PLAIN=${DIST_PLAIN:-$BUILT/dist-$PLAIN_V}
if [ "$LEG" != s9 ]; then
  build_wheel "$OLD_V" "$DIST_OLD"
  build_wheel "$PLAIN_V" "$DIST_PLAIN"
  build_wheel "$BROKEN_V" "$DIST_BROKEN" break
  # The broken wheel must be a real wheel of the right version, or s5
  # would prove nothing about a broken release.
  compgen -G "$DIST_BROKEN/vibe_seller-$BROKEN_V-*.whl" >/dev/null \
    || fail "no $BROKEN_V wheel built"
fi
build_wheel "$NEW_V" "$DIST_NEW"

# ── the leg runner ─────────────────────────────────────────────────
run_leg() { # run_leg <leg> <target-version> <dist-dir> [extra env…]
  local leg=$1 target=$2 dist=$3
  local from_pypi=0
  if [ "$leg" = s9 ]; then
    # The gate leg starts from a PUBLISHED release, not from a build of
    # this checkout: what is installed is what a customer has, and the
    # only thing under test is whether THAT can move onto the candidate
    # by itself. OLD_V becomes that version.
    [ -n "${PREV_V:-}" ] || fail "LEG=s9 needs PREV_V=<published version>"
    OLD_V="$PREV_V"
    from_pypi=1
  fi
  PORT=$((BASE_PORT + LEG_INDEX)); LEG_INDEX=$((LEG_INDEX + 1))
  echo ""
  echo "################ LEG $leg (port $PORT, target $target, old $OLD_V)"
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
  # The mock CLI belongs to the SERVER's environment, because the daemon
  # is what spawns the agent for a task — exporting it inside leg s2,
  # after `vibe-seller start`, meant the daemon had no mock and the
  # agent never came up at all (measured in CI on ubuntu AND macOS:
  # "no agent became live"). Only s2 creates a task, so this is inert
  # for every other leg; s5b unsets it before hop 2 on purpose.
  export MOCK_CLI="$REPO/tests/e2e/mock_cli.py"
  # `min_run_seconds` is the leg's hold on TIME, not on steps: leg s2
  # needs a task that keeps the server busy for longer than two idle
  # intervals, and pacing that with `delay` would pace the planning
  # phase too (a 4-minute wait for a plan nobody is reviewing yet).
  export MOCK_CLI_SCENARIO='{"result":"still working","delay":10,"min_run_seconds":260}'
  mkdir -p "$VIBE_HOME"

  # The mock PyPI document: the target is the newest stable release.
  local mock="$WORK/pypi.json"
  cat > "$mock" <<EOF
{"info": {"version": "$target"},
 "releases": {"$OLD_V": [{"yanked": false}], "$target": [{"yanked": false}]}}
EOF
  export VIBE_SELLER_PYPI_JSON_URL="$mock"
  # ALL THREE wheel sets are in the sources on purpose, including the
  # BROKEN build, which is a HIGHER version than what legs s1 and s2
  # offer. That is the point: an apply must install the release the
  # update check APPROVED, not whatever the sources call newest, and
  # the only way to prove it here is to leave a newer, wrong answer
  # lying around. Measured on 2026-11-13 with the unpinned command the
  # leg did exactly that — `uv tool upgrade` installed 99.0.3 from this
  # list, the server died on boot with "fabricated broken release", and
  # last.json described an update to 99.0.2 that never happened. The fix
  # is in the product (`vibe-seller upgrade --version`, app/cli.py
  # `_upgrade_to_exact`); these legs are its negative control.
  if [ "$from_pypi" = 1 ]; then
    # Only the candidate exists locally on the gate leg; naming the OLD
    # and BROKEN dirs it never installs would point uv at directories
    # this run never built.
    export UV_FIND_LINKS="$dist"
  else
    export UV_FIND_LINKS="$DIST_OLD,$dist,$DIST_BROKEN"
  fi

  ADMIN_JAR="$WORK/admin.cookies"; MEMBER_JAR="$WORK/member.cookies"
  LOG="$VIBE_HOME/logs/backend_$PORT.log"

  if [ -n "$(version_on_port)" ]; then
    fail "port $PORT is already serving — pick another with PORT=<n>"
  fi

  echo "-- install $OLD_V and start"
  if [ "$from_pypi" = 1 ]; then
    uv tool install -q --force "vibe-seller==$OLD_V" \
      || fail "could not install the published $OLD_V from PyPI"
    # A published release older than the idle-apply loop has nothing
    # that can apply an update by itself, so this leg would be testing a
    # click — #244's, not this feature's. Skip, and say what was checked.
    #
    # `GATE_FORCE=true` is the release gate asking to be shown the
    # failure rather than the skip: the gate then reports the CONDITION
    # it would have skipped for, from the leg that hit it, and a nobody
    # has ever run this job claim stops being true. Measured on
    # 2026-11-13: workflow_dispatch of pip-upgrade-gate.yml itself is
    # impossible until the file is on the default branch (GitHub answers
    # HTTP 404), so `cli-upgrade.yml`'s `gate-rehearsal` job is what
    # runs these legs on CI during the PR.
    if ! compgen -G \
      "$UV_TOOL_DIR/vibe-seller/lib/python*/site-packages/app/update_idle.py" \
      >/dev/null; then
      skip="vibe-seller $OLD_V has no app/update_idle.py — that install"
      skip+=" cannot self-apply, so there is no pip path to gate on"
      if [ "${GATE_FORCE:-false}" = true ]; then
        # `fail` and not `return 1`: the driver runs legs in a loop
        # under `set -u`, so a returned 1 would be swallowed and the
        # whole script would still print PASS at the end.
        fail "forced through the skip condition: $skip"
      fi
      echo "SKIP: $skip"
      stop_leg
      return 0
    fi
  else
    uv tool install -q --force --find-links "$DIST_OLD" "vibe-seller==$OLD_V" \
      || fail "could not install $OLD_V"
    compgen -G "$UV_TOOL_DIR/vibe-seller/lib/python*/site-packages/app/auto_update.py" \
      >/dev/null || fail "installed $OLD_V is not this checkout's code"
  fi
  # An unpinned receipt, like install.sh leaves: a pin would freeze every
  # later `uv tool upgrade` at this version.
  local receipt="$UV_TOOL_DIR/vibe-seller/uv-receipt.toml"
  [ -f "$receipt" ] || fail "no uv receipt — not a uv tool install"
  sed -i.bak 's/, specifier = "==[^"]*"//' "$receipt" && rm -f "$receipt.bak" \
    || fail "could not unpin the receipt"
  grep -q 'specifier' "$receipt" && fail "receipt still pinned"
  [ "$(vibe-seller --version | awk '{print $NF}')" = "$OLD_V" ] \
    || fail "installed CLI is not $OLD_V"

  if [ "$leg" = s7 ]; then
    # A dev-shaped version: setuptools-scm's own shape, not a nightly.
    echo "-- s7 reinstalls as a DEV build and expects to be left alone"
    # Nothing may be running from the tool env while `uv tool install`
    # replaces it: a live server keeps the receipt and the venv under
    # it, and a reinstall that lands half-way leaves the leg testing an
    # install that is neither OLD nor the dev build.
    vibe-seller stop --port "$PORT" 2>/dev/null
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
    do_logins
    assert_idle_no_update "a dev build"
    stop_leg
    return 0
  fi

  vibe-seller start --port "$PORT" || fail "could not start $OLD_V"
  wait_for_version "$OLD_V" || fail "$OLD_V never came up on $PORT"
  do_logins
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
      # The bound is the measured sequence, not a guess: first check 60 s
      # after boot, then the idle tick (up to 60 s more), then the apply
      # itself — a real `vibe-seller upgrade`, which also moves Chromium
      # and the Claude Code CLI. s1 measured 154.9 s from the tick to the
      # record on 2026-11-13, so 200 s left no room for a slow npm.
      wait_for_version "$target" 360 \
        || fail "the idle apply never happened (port still $(version_on_port))"
      [ "$(boots)" -gt "$BOOTS_BEFORE" ] || fail "no second boot in $LOG"
      # The server that restarted is the NEW one; it must say so.
      [ "$(update_field state)" = idle ] \
        || fail "the NEW server is not idle: $(status_json)"
      wait_for_record || fail "the apply never wrote a record"
      local rec
      rec=$(record_field status)
      [ "$rec" = ok ] || fail "the apply record says '$rec': $(record_json)"
      [ "$(record_field path)" = pip ] || fail "record path != pip"
      [ "$(record_field trigger)" = auto_idle ] \
        || fail "record trigger != auto_idle: $(record_json)"
      echo "   OK: applied itself onto $target, recorded pip/auto_idle/ok"

      # The apply installed $target BY NAME (app/pip_update.py hands
      # `vibe-seller upgrade --version` to the helper, review 4's
      # sibling), and uv writes what it is told into the tool receipt —
      # where a `==` would freeze EVERY later `uv tool upgrade` at this
      # version, which is exactly why app/cli.py's own upgrade path
      # goes unpinned. So the pin this apply needs and the pin that
      # would quietly break the user's next manual upgrade are
      # separated by one line of receipt repair (_unpin_tool_receipt),
      # and the only honest proof is to do the user's next upgrade for
      # real: plain, no version, a newer wheel available.
      echo "-- a plain 'vibe-seller upgrade' still moves after that apply"
      local receipt
      receipt=$(ls "$UV_TOOL_DIR"/vibe-seller/uv-receipt.toml 2>/dev/null)
      [ -n "$receipt" ] || fail "no uv receipt to read"
      if grep -q 'specifier' "$receipt"; then
        fail "the pinned apply left its specifier in $receipt"
      fi
      stop_server_only
      UV_FIND_LINKS="$DIST_PLAIN" vibe-seller upgrade \
        > "$WORK/plain-upgrade.log" 2>&1 \
        || fail "the plain upgrade failed: $(tail -5 "$WORK/plain-upgrade.log")"
      [ "$(installed_version)" = "$PLAIN_V" ] \
        || fail "a plain upgrade stopped at $(installed_version), not $PLAIN_V"
      echo "   OK: receipt unpinned, plain upgrade moved to $PLAIN_V"
      ;;
    s2)
      echo "-- S2: occupy the server with a task, and check it waits"
      # MOCK_CLI / MOCK_CLI_SCENARIO are already in the environment this
      # leg's server was started with (see run_leg): the daemon spawns
      # the agent, not this script.
      #
      # This task is a NON-STORE one, and a non-store task is always a
      # plan-mode task (app/routers/tasks.py, "Non-store tasks always
      # plan mode"). So `plan_mode:false` in the body is not a request
      # the server honours, and waiting for the task to finish by
      # itself — what this leg used to do — waits for a turn that can
      # never start: the agent plans, reaches PLANNED, and holds there
      # for a human. Measured twice on 2026-11-13, in CI and here:
      # `FAIL: the mock task never finished` after 240 s, with the
      # server correctly refusing to apply over a DESIGNING task the
      # whole time. The leg now sends what the UI sends, including the
      # approval: create → PLANNED → POST /execute-plan → RUNNING.
      local task tid st
      task=$(api "$ADMIN_JAR" POST /api/tasks \
        '{"title":"hold the server","description":"keep running","plan_mode":false}') \
        || fail "could not create the task"
      tid=$(echo "$task" | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
      local live waited=0
      while [ $waited -lt 60 ]; do
        live=$(update_field running_tasks)
        [ "$live" != 0 ] && break
        sleep 1; waited=$((waited + 1))
      done
      [ "$live" != 0 ] \
        || fail "creating a task did not make the server busy (status $(task_status "$ADMIN_JAR" "$tid")); MOCK_CLI=$MOCK_CLI"
      echo "   the task is live (running_tasks=$live)"
      # The plan must actually arrive: that is what gates the approval,
      # and a leg that approved a task still planning would be testing
      # an endpoint the UI only shows once there is a plan to approve.
      st=$(wait_for_task_status "$ADMIN_JAR" "$tid" planned 240) \
        || fail "the mock never reached planned (last status: $st)"
      echo "   planned — approving the way the UI does"
      api "$ADMIN_JAR" POST "/api/tasks/$tid/execute-plan" >/dev/null \
        || fail "POST /execute-plan was refused at status $st"
      st=$(wait_for_task_status "$ADMIN_JAR" "$tid" running 120) \
        || fail "the approved plan never started executing (last: $st)"
      echo "   executing (status=$st); busy for two idle intervals now"
      # Busy for a good two idle-check intervals: nothing may move.
      local before=$(port_version) held=0
      while [ $held -lt 150 ]; do
        [ "$(port_version)" = "$before" ] \
          || fail "the update applied over a running task (status $(task_status "$ADMIN_JAR" "$tid"))"
        [ "$(installed_version)" = "$OLD_V" ] \
          || fail "the install moved while a task was running"
        sleep 2; held=$((held + 2))
        [ $held -eq 60 ] && echo "   still $before at +60s (correct)"
      done
      # And the window has to have been REAL: a task that ended inside
      # the hold would let the apply land during it and the leg would
      # then be measuring nothing.
      st=$(task_status "$ADMIN_JAR" "$tid")
      case "$st" in
        completed|failed)
          fail "S2 proved nothing: the task ended ($st) inside the 150s hold"
          ;;
      esac
      echo "   held for ${held}s with the task $st — nothing applied"
      st=$(wait_for_task_done "$ADMIN_JAR" "$tid") \
        || fail "the mock task never finished (last status: $st)"
      wait_for_version "$target" 360 \
        || fail "after the task ended the update still did not apply"
      echo "   OK: waited for the task, then applied onto $target by itself"
      ;;
    s5|s5b|s5c)
      echo "-- $leg: the target will not boot; the machine must come back"
      local expect_from=$OLD_V
      [ "$leg" = s5b ] && expect_from=$NEW_V
      if [ "$leg" = s5b ]; then
        echo "   hop 1: $OLD_V -> $NEW_V (a good release)"
        wait_for_version "$NEW_V" 360 || fail "hop 1 never applied"
        stop_server_only
        # Same install tree, new target: the BROKEN wheel. Re-point the
        # mock index and restart, so the update check offers 99.0.3.
        cat > "$mock" <<EOF
{"info": {"version": "$BROKEN_V"},
 "releases": {"$NEW_V": [{"yanked": false}], "$BROKEN_V": [{"yanked": false}]}}
EOF
        # The BROKEN build was already in the sources (see run_leg);
        # what changes here is the OFFER. And the rollback after it has
        # to find $NEW_V, which by then exists only in the uv cache —
        # never in a directory anyone handed uv — which is the whole
        # subject of this leg.
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
      # WHAT THIS LEG MUST WAIT ON, and what it used to wait on.
      #
      # Waiting for the port to answer $expect_from proves nothing,
      # because the port answers $expect_from BEFORE the apply starts:
      # that is the version the machine is on. In s5b it is true twice
      # over (hop 1 ended on it), and the 15:24 run of 2026-11-13
      # showed what the leg then asserts: hop 1's `ok` record, read as
      # if it were hop 2's verdict — `FAIL: the record should say
      # rolled_back: {… "status": "ok", "to": "99.152433.2"}` — one
      # second after the broken release went ready and before any apply
      # had been spawned at all.
      #
      # So the leg waits on the two events that only an APPLY produces,
      # in order: the helper logged a new `applying` line, and a NEW
      # last.json landed. An apply that dies runs out the helper's own
      # health bound (HEALTH_TIMEOUT, 300 s) before it starts putting
      # the old version back, then reinstalls and boots — so the second
      # wait is bounded by the helper's budget plus one boot, not by
      # hope.
      local applies_before record_before
      applies_before=$(applies_started)
      record_before=$(record_mtime_ns)
      wait_for_apply_start "$applies_before" 150 \
        || fail "the idle tick never started an apply for $target"
      wait_for_new_record "$record_before" \
        || fail "the apply for $target never wrote a record"
      [ "$(record_field to)" = "$target" ] \
        || fail "the new record is about $(record_field to), not $target: $(record_json)"
      # Now, and only now, is "the machine came back" a question with an
      # answer: the apply is over, so whatever serves the port is the
      # result of the rollback.
      if ! wait_for_version "$expect_from" 60; then
        dump_apply_state
        fail "after the rolled-back apply the port answers \
$(port_version || echo DOWN), not $expect_from"
      fi
      health_ok || fail "the server answers /api/version but not /api/health"
      [ "$(installed_version)" = "$expect_from" ] \
        || fail "the INSTALL is still $(installed_version) — no real rollback"
      [ "$(boots)" -gt "$BOOTS_BEFORE" ] \
        || fail "no new boot in $LOG after the rollback ($(boots) boots, expected > $BOOTS_BEFORE)"
      [ "$(record_field status)" = rolled_back ] \
        || fail "the record should say rolled_back: $(record_json)"
      [ "$(record_field trigger)" = auto_idle ] \
        || fail "the outage was not an auto_idle apply: $(record_json)"
      echo "   OK: the broken release failed, $expect_from is serving again"
      echo "   record: $(record_json)"
      if [ "$leg" = s5c ]; then
        # S5c — review 4's outage loop. The next check cycle (6 h in
        # production, ~60 s after a boot here) offers this very release
        # again: nothing about it changed. Warming it and marking it
        # ready is fine; APPLYING it again is a second outage, and the
        # third one comes six hours later, forever, for a build that
        # cannot boot. So a boot is how this leg fast-forwards the
        # cycle, and the assertion is that the idle tick REFUSES.
        echo "-- S5c: the same release must never be applied a second time"
        local applies_before record_before
        applies_before=$(applies_started)
        record_before=$(record_json)
        stop_server_only
        vibe-seller start --port "$PORT" || fail "s5c restart failed"
        wait_for_version "$expect_from" \
          || fail "s5c: $expect_from never came back up after the restart"
        state=''
        for _ in $(seq 1 200); do
          state=$(update_field state)
          [ "$state" = ready ] && break
          sleep 2
        done
        [ "$state" = ready ] \
          || fail "s5c: the broken release was never offered again ($state)"
        echo "   offered again and ready (as the bug would)"
        # Two idle intervals past that: the tick must refuse it.
        sleep 150
        [ "$(port_version)" = "$expect_from" ] \
          || fail "s5c: it applied the rolled-back release a second time"
        local applies_after
        applies_after=$(grep -c 'applying ' \
          "$VIBE_HOME/updates/pip-apply.log" 2>/dev/null || echo 0)
        [ "$applies_after" = "$applies_before" ] \
          || fail "s5c: a second apply started ($applies_before -> $applies_after)"
        [ "$(record_json)" = "$record_before" ] \
          || fail "s5c: the record was rewritten — a second outage happened"
        [ "$(installed_version)" = "$expect_from" ] \
          || fail "s5c: the install moved to $(installed_version)"
        [ "$(update_field auto_apply)" = false ] \
          || fail "s5c: a rolled-back release still promises to apply itself"
        [ "$(update_field refusal.version)" = "$BROKEN_V" ] \
          || fail "s5c: no refusal reported for $BROKEN_V: $(status_json)"
        [ "$(update_field popup_due)" = true ] \
          || fail "s5c: the admin was never asked to decide"
        echo "   OK: refused across a fresh boot — one outage per bad release"
      fi
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
  # The status code, not just "curl exited 22": a login can fail because
  # nothing serves the port yet, because the credentials are not the
  # seeded ones, or because auth is off, and `curl -f` calls all three
  # the same. What the port answers is printed too, so the next failure
  # is readable without rerunning anything.
  local resp code
  resp=$(curl -sS --noproxy '*' --max-time 20 -c "$1" -X POST \
    "http://127.0.0.1:$PORT/api/auth/login" \
    -H 'Content-Type: application/json' -w '\n%{http_code}' \
    -d "{\"identifier\": \"$2\", \"password\": \"$3\"}") || resp=''
  code=${resp##*$'\n'}
  [ "$code" = 200 ] && return 0
  fail "login failed for $2 (HTTP ${code:-no-answer}; /api/version says '$(version_on_port | head -c 160)'; body ${resp:0:200})"
}

do_logins() {
  # Every leg needs a session: /api/system/update-status is behind
  # auth, and a leg that cannot read it proves nothing. This runs only
  # once a server is SERVING — the admin row is created by the first
  # boot of this VIBE_HOME's database, so a login attempted before that
  # boot has no user to authenticate and no server to answer it.
  echo "-- logins"
  login "$ADMIN_JAR" admin admin
  api "$ADMIN_JAR" POST /api/users \
    '{"username":"auto-member","email":"a-m@example.com","password":"memberpass123","role":"member"}' \
    >/dev/null 2>&1 || echo "   (member exists — reusing)"
  login "$MEMBER_JAR" auto-member memberpass123
}
json_field() {
  # Scalars print in JSON's own spelling — true / false / null, not
  # Python's True / False / None — because that is what the API document
  # says, and an assertion that only matches under one of the two
  # spellings is a leg that fails for the wrong reason (s1's auto_apply
  # check was exactly that: it could never pass).
  #
  # No backticks anywhere inside this string: they would be command
  # substitutions to the shell, not prose to Python.
  python3 -c "
import json, sys
v = json.load(sys.stdin)
for k in '$1'.split('.'):
    v = v.get(k) if isinstance(v, dict) else None
print(json.dumps(v) if isinstance(v, (bool, type(None))) else v)
" 2>/dev/null
}
version_on_port() {
  # The RAW body, for liveness only: `curl -f` is non-empty exactly when
  # something answers. Anything that wants the VERSION uses
  # port_version — comparing this body to a version string is how leg s5
  # once reported "never came back on 99.70300.1 (now:
  # {version: 99.70300.1})", a message that contradicted itself because
  # the machine had come back correctly.
  curl -fsS --noproxy '*' --max-time 3 "http://127.0.0.1:$PORT/api/version" 2>/dev/null
}
port_version() { version_on_port | json_field version; }

dump_apply_state() { # every sub-condition a leg can fail on, at once
  echo "   --- apply state" >&2
  echo "   port answers: $(version_on_port | head -c 200)" >&2
  echo "   parsed version: $(port_version) / installed: $(installed_version)" >&2
  echo "   boots in this log: $(boots) (expected > ${BOOTS_BEFORE:-?})" >&2
  echo "   update-status: $(status_json | head -c 400)" >&2
  echo "   last.json: $(record_json | head -c 600)" >&2
  if [ -f "$VIBE_HOME/updates/pip-apply.log" ]; then
    echo "   applies started: $(grep -c 'applying ' "$VIBE_HOME/updates/pip-apply.log")" >&2
    echo "   --- pip-apply.log (last 12)" >&2
    tail -12 "$VIBE_HOME/updates/pip-apply.log" >&2
  else
    echo "   pip-apply.log: ABSENT — the helper was never spawned" >&2
  fi
  if [ -f "${LOG:-}" ]; then
    echo "   --- backend log: apply/idle/restart lines (last 12)" >&2
    grep -E "auto-apply tick|applying v|Re-executing|Upgrade restart|update-status|rolled_back"       "$LOG" 2>/dev/null | tail -12 >&2
  fi
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
boots() {
  # `grep -c` prints its own 0 when the file exists and matches
  # nothing, so `|| echo 0` would answer "0\n0" — and every
  # `[ "$(boots)" -gt N ]` comparison would then fail with
  # "integer expression expected", which is a leg that cannot see a
  # boot even when one happened.
  local n
  n=$(grep -c 'Application startup complete' "$LOG" 2>/dev/null) || n=0
  echo "${n:-0}"
}
wait_for_version() {
  local want=$1 tries=${2:-90} got i
  for i in $(seq 1 "$tries"); do
    got=$(port_version)
    [ "$got" = "$want" ] && return 0
    sleep 1
  done
  echo "   port $PORT reports: ${got:-<nothing>}" >&2
  return 1
}
wait_for_record() { # the apply is not over when the VERSION moves
  # `last.json` is the LAST thing the helper writes — after its own
  # health check, after the rollback when there is one — so a leg that
  # reads it the second the port answers the new version reads a half
  # finished apply. Measured on 2026-11-13 in leg s1: the server was
  # already serving 99.0.2 (it restarts itself onto the new install)
  # while the upgrade command was still moving Chromium and the Claude
  # Code CLI, and the record landed 40 s later.
  local tries=${1:-200}
  for _ in $(seq 1 "$tries"); do
    [ -s "$VIBE_HOME/updates/last.json" ] && return 0
    sleep 2
  done
  return 1
}

wait_for_task_done() { # wait_for_task_done <jar> <task id>
  local jar=$1 tid=$2 st
  for _ in $(seq 1 200); do
    st=$(api "$jar" GET "/api/tasks/$tid" | json_field status)
    case $st in completed|failed) echo "$st"; return 0 ;; esac
    sleep 2
  done
  echo "${st:-unreadable}"
  return 1
}
task_status() { # task_status <jar> <task id>
  api "$1" GET "/api/tasks/$2" 2>/dev/null | json_field status
}
wait_for_task_status() { # <jar> <id> <want> <tries> — prints the last status seen
  local jar=$1 tid=$2 want=$3 tries=${4:-90} st i
  for i in $(seq 1 "$tries"); do
    st=$(task_status "$jar" "$tid")
    [ "$st" = "$want" ] && { echo "$st"; return 0; }
    case $st in completed|failed) break ;; esac
    sleep 2
  done
  echo "${st:-unreadable}"
  return 1
}
applies_started() {
  # How many applies the helper has been SPAWNED for, counted in its own
  # log. The one signal that separates "the idle tick never fired" from
  # "the apply ran and failed inside": the version the port answers
  # cannot tell those apart, because a rolled-back apply leaves the SAME
  # version serving that it started with.
  local n
  n=$(grep -c 'applying ' "$VIBE_HOME/updates/pip-apply.log" 2>/dev/null) || n=0
  echo "${n:-0}"
}
wait_for_apply_start() { # <count-before> <tries>
  local before=$1 tries=${2:-150} now i
  for i in $(seq 1 "$tries"); do
    now=$(applies_started)
    [ "$now" -gt "$before" ] && return 0
    sleep 2
  done
  echo "   pip-apply.log still holds $(applies_started) apply line(s), was $before" >&2
  return 1
}
record_mtime_ns() {
  # Nanoseconds, so two records in the same second cannot tie; 0 when
  # there is no record at all.
  python3 -c '
import os, sys
try:
    print(os.stat(sys.argv[1]).st_mtime_ns)
except OSError:
    print(0)
' "$VIBE_HOME/updates/last.json" 2>/dev/null || echo 0
}
wait_for_new_record() { # <mtime-ns-before> <tries>
  local before=$1 tries=${2:-400} now
  for _ in $(seq 1 "$tries"); do
    now=$(record_mtime_ns)
    [ "${now:-0}" -gt "${before:-0}" ] && return 0
    sleep 2
  done
  echo "   last.json mtime still ${now:-0} ns (was $before) — no apply finished" >&2
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
  [ "$(update_field version)" = null ] || fail "offered $(update_field version)"
  [ "$(update_field supported)" = false ] \
    || fail "a dev build reports supported"
  echo "   OK: $who stayed idle and unsupported for 180s"
}
build_dev_wheel() { # a setuptools-scm dev shape, not a nightly
  local out=$1
  if [ -d "$out" ] && compgen -G "$out/*.whl" >/dev/null; then return 0; fi
  mkdir -p "$out"
  (cd "$REPO" && SETUPTOOLS_SCM_PRETEND_VERSION=$DEV_V \
    uv build --wheel -q -o "$out") || fail "could not build the dev wheel"
}
stop_server_only() {
  # The server down, the data root INTACT. s5b hops twice over one
  # install, so its interlude cannot be a `stop_leg`: that also deletes
  # VIBE_HOME, the seeded admin, the mock index and the cookie jars, and
  # the second hop would boot a machine with no database behind it.
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
}

stop_leg() {
  [ "$KEEP" = 1 ] && { echo "   KEEP=1: leaving $PORT up in $WORK"; return 0; }
  stop_server_only
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
    # s5b starts from the GOOD release: hop 1 must be a real pip update
    # whose version is what the rollback has to find again. Offering it
    # the broken build from the first boot would test s5 twice.
    s5b) run_leg s5b "$NEW_V" "$DIST_NEW" ;;
    s5c) run_leg s5c "$BROKEN_V" "$DIST_BROKEN" ;;
    s7) run_leg s7 "$NEW_V" "$DIST_NEW" ;;
    s9) run_leg s9 "$NEW_V" "$DIST_NEW" ;;
    all) run_leg s1 "$NEW_V" "$DIST_NEW"
         run_leg s2 "$NEW_V" "$DIST_NEW"
         run_leg s5 "$BROKEN_V" "$DIST_BROKEN"
         run_leg s5b "$NEW_V" "$DIST_NEW"
         run_leg s5c "$BROKEN_V" "$DIST_BROKEN"
         run_leg s7 "$NEW_V" "$DIST_NEW" ;;
    *) fail "unknown LEG '$one' (s1 s2 s5 s5b s5c s7 s9 all)" ;;
  esac
done

echo ""
echo "PASS: legs ($LEG) — auto-update end to end on $(uname)"
