#!/usr/bin/env bash
# The integration no-regression smoke, POSIX edition (WSL / macOS).
#
# Owns the server (start.sh, its own VIBE_HOME + port + BACKEND_PORT),
# runs the driver's main phase, restarts the server, then runs the
# resume phase (the WAITING task must survive the restart).
#
# PORT OWNERSHIP IS PROVEN, not assumed: other sessions on this box
# run e2e servers that pick "free" ports, and one can land on ours in
# the window between start.sh's port check and uvicorn's bind — a
# health check then passes against THEIR server (observed: our uvicorn
# died with EADDRINUSE while the driver drove the foreign server). So
# after health passes, the LISTENER's environment must carry our
# VIBE_HOME before anything else runs; a foreign owner aborts the port
# and the next candidate is tried.
#
# Usage:
#   MINIMAX_API_KEY=… [MINIMAX_BASE_URL=… MINIMAX_MODEL=…] \
#   tests/e2e/smoke/run_smoke.sh [port] [--with-browser]
#
# Everything it needs from the outside is the MiniMax secrets (env —
# never arguments, never files). The evidence lands under
# /tmp/vibe-smoke-<port>/ (task ids, log excerpts, the fake SMTP log).

set -euo pipefail

PORT="${1:-7863}"
shift || true
WITH_BROWSER=0
for arg in "$@"; do
  case "$arg" in
    --with-browser) WITH_BROWSER=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
OUT="${SMOKE_OUT:-/tmp/vibe-smoke-$PORT}"
HOME_DIR="$OUT/home"
ADS_PORT="${SMOKE_ADS_PORT:-17891}"
# C1's per-instance CDP range: the smoke's store browser gets its own
# ports on a shared machine (only used for --with-browser).
export VIBE_CDP_PROXY_BASE="${VIBE_CDP_PROXY_BASE:-9530}"

cd "$ROOT"
mkdir -p "$OUT"

# ── the MiniMax secrets must be present, never written anywhere ────
if [ -z "${MINIMAX_API_KEY:-}" ]; then
  echo 'MINIMAX_API_KEY is not set — the smoke runs real tasks' >&2
  exit 2
fi

start_server() {
  VIBE_HOME="$HOME_DIR" \
  VIBE_SELLER_TELEMETRY=0 \
  VIBE_EXTERNAL_SYNC=off \
  VIBE_GITHUB_MAIN_SYNC=off \
  VIBE_ADS_SERVICE_URL="http://127.0.0.1:$ADS_PORT" \
  VIBE_CDP_PROXY_BASE="$VIBE_CDP_PROXY_BASE" \
  BACKEND_PORT="$PORT" \
  VIBE_SKIP_FRONTEND_BUILD=1 \
    ./start.sh "$PORT" > /dev/null 2>&1
  for _ in $(seq 1 90); do
    if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo 'server did not come up' >&2
  return 1
}

# The listener on $PORT must be OUR server: its process environment
# carries our VIBE_HOME. A foreign server (another session's e2e that
# won the bind race) answers health just as well — this is the tell.
verify_ownership() {
  local pid
  pid=$(ss -tlnp "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  if [ -z "$pid" ]; then
    echo "no listener on $PORT to verify" >&2
    return 1
  fi
  if ! tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null \
      | grep -qx "VIBE_HOME=$HOME_DIR"; then
    echo "port $PORT is owned by pid $pid whose env does NOT carry our" \
         "VIBE_HOME — a foreign server is answering; aborting this port" >&2
    return 1
  fi
  echo "port $PORT owned by pid $pid (VIBE_HOME verified)"
}

stop_server() {
  # --scoped: this home's leftovers only, never another instance's
  ./stop.sh "$PORT" --scoped > /dev/null 2>&1 || true
}

cleanup() {
  stop_server
}
trap cleanup EXIT

# ── server up, on a port we PROVE is ours (up to three candidates) ──
PORT_OK=0
for CANDIDATE in "$PORT" "$((PORT + 1))" "$((PORT + 2))"; do
  PORT="$CANDIDATE"
  OUT="${SMOKE_OUT:-/tmp/vibe-smoke-$PORT}"
  HOME_DIR="$OUT/home"
  mkdir -p "$OUT"
  if ss -tln "sport = :$PORT" 2>/dev/null | grep -q LISTEN; then
    echo "port $PORT is already listening — trying the next" >&2
    continue
  fi
  if start_server && verify_ownership; then
    PORT_OK=1
    break
  fi
  stop_server
  sleep 2
done
if [ "$PORT_OK" != 1 ]; then
  echo 'no port could be owned — giving up' >&2
  exit 2
fi
echo "server up on $PORT (VIBE_HOME=$HOME_DIR)"

# the server's own file log, where the guard/tripwire lines land
# (start.sh puts it under the CHECKOUT's logs/, keyed by port)
APP_LOG="$ROOT/logs/backend_$PORT.log"
echo "server log: $APP_LOG"

DRIVER_ARGS=(--phase main --base-url "http://127.0.0.1:$PORT"
             --home "$HOME_DIR" --server-log "$APP_LOG" --out "$OUT"
             --ads-port "$ADS_PORT")
if [ "$WITH_BROWSER" = 1 ]; then
  DRIVER_ARGS+=(--with-browser)
fi

VIBE_HOME="$HOME_DIR" \
MINIMAX_API_KEY="$MINIMAX_API_KEY" \
MINIMAX_BASE_URL="${MINIMAX_BASE_URL:-}" \
MINIMAX_MODEL="${MINIMAX_MODEL:-}" \
VIBE_ADS_SERVICE_URL="http://127.0.0.1:$ADS_PORT" \
VIBE_CDP_PROXY_BASE="$VIBE_CDP_PROXY_BASE" \
  "$ROOT/.venv/bin/python" -m tests.e2e.integration_smoke "${DRIVER_ARGS[@]}"

# ── the restart: the WAITING task must survive it ──────────────────
echo 'restarting the server (--scoped)'
stop_server
start_server
verify_ownership
echo 'server restarted'

VIBE_HOME="$HOME_DIR" \
MINIMAX_API_KEY="$MINIMAX_API_KEY" \
MINIMAX_BASE_URL="${MINIMAX_BASE_URL:-}" \
MINIMAX_MODEL="${MINIMAX_MODEL:-}" \
VIBE_ADS_SERVICE_URL="http://127.0.0.1:$ADS_PORT" \
  "$ROOT/.venv/bin/python" -m tests.e2e.integration_smoke \
    --phase resume --base-url "http://127.0.0.1:$PORT" \
    --home "$HOME_DIR" --server-log "$APP_LOG" --out "$OUT" \
    --ads-port "$ADS_PORT"

echo "SMOKE COMPLETE — evidence in $OUT"
