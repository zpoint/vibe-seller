#!/bin/bash

# Start vibe-seller server
# Usage: ./start.sh           # default port 7777
#        ./start.sh 8080     # port 8080
#        ./start.sh --dev    # dev mode (DEBUG logs + AGENT_DEBUG)
#        PORT=7780 ./start.sh # use env var

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# -- Color output (only when interactive) --
if [[ -t 2 ]]; then
    _R=$'\033[31m' _G=$'\033[32m' _B=$'\033[34m'
    _W=$'\033[1m' _Z=$'\033[0m'
else
    _R='' _G='' _B='' _W='' _Z=''
fi

_info()  { printf "%s==>%s %s%s\n" "$_B$_W" "$_Z$_W" "$*" "$_Z"; }
_error() { printf "%sError%s: %s\n" "$_R$_W" "$_Z" "$*" >&2; }

# -- Make installed tools resolvable in THIS shell --
# install.sh drops uv in ~/.local/bin (and node/pnpm under the npm
# prefix); a login shell may not have those dirs on PATH, so `uv` would
# be missing even though the dependency check below — which bootstraps
# the same dirs internally — passes. Ask the installer for its canonical
# PATH so the check-context and the run-context are identical. Without
# this, `env … uv run …` at launch fails with "env: uv: No such file".
if _bootstrap_path="$("$SCRIPT_DIR/install.sh" --print-path 2>/dev/null)" \
        && [ -n "$_bootstrap_path" ]; then
    export PATH="$_bootstrap_path"
fi

# -- Prerequisite checks (delegates to install.sh) --
if ! "$SCRIPT_DIR/install.sh" --check-only; then
    echo "" >&2
    _error "Run \"$SCRIPT_DIR/install.sh\" to install missing dependencies"
    exit 1
fi

# Ensure venv exists (uv downloads Python 3.11+ automatically)
VENV_DIR="${VIBE_SELLER_VENV:-$SCRIPT_DIR/.venv}"
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    _info "Creating virtual environment (uv will fetch Python if needed)..."
    uv venv --python ">=3.11" "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

if [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
    # Install/update Python deps if pyproject.toml is newer than marker
    MARKER="$VENV_DIR/.deps_installed"
    if [ ! -f "$MARKER" ] || [ "$SCRIPT_DIR/pyproject.toml" -nt "$MARKER" ]; then
        _info "Installing Python dependencies..."
        uv pip install -e "$SCRIPT_DIR" --quiet && touch "$MARKER"
    fi
fi

# Parse --dev flag
DEV_MODE=false
for arg in "$@"; do
    if [ "$arg" = "--dev" ]; then
        DEV_MODE=true
    fi
done

if $DEV_MODE; then
    export LOG_LEVEL=DEBUG
    export AGENT_DEBUG=1
    _info "Dev mode: LOG_LEVEL=DEBUG, AGENT_DEBUG=1"
fi

# Port: positional arg (skip flags) > env var > default 7777
_port=""
for arg in "$@"; do
    if [ "$arg" != "--dev" ]; then
        _port="$arg"
        break
    fi
done
PORT="${_port:-${PORT:-7777}}"

# Check if port is occupied
if lsof -ti:"$PORT" >/dev/null 2>&1; then
    _error "Port $PORT is already in use. Stop the existing server or choose another port."
    exit 1
fi

# Build frontend
FRONTEND_DIR="$SCRIPT_DIR/frontend"
if [ -f "$FRONTEND_DIR/package.json" ]; then
    echo "Building frontend SPA..."

    # Install/update dependencies if package.json is newer than node_modules
    if [ ! -d "$FRONTEND_DIR/node_modules" ] || [ "$FRONTEND_DIR/package.json" -nt "$FRONTEND_DIR/node_modules/.package-lock.json" ] 2>/dev/null; then
        echo "Installing frontend dependencies..."
        (cd "$FRONTEND_DIR" && pnpm install)
    fi

    # Build frontend (pnpm build → frontend/dist/)
    (cd "$FRONTEND_DIR" && pnpm build)

    if [ $? -eq 0 ]; then
        echo "Frontend build complete → frontend/dist/"
    else
        echo "WARNING: Frontend build failed."
    fi
fi

# Start backend only (serves frontend static files)
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

echo "Starting server on port $PORT..."
cd "$SCRIPT_DIR"
nohup env LOG_DIR="$LOG_DIR" BACKEND_PORT="$PORT" uv run python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

# Poll /api/health until the socket is bound and the app responds.
# A bare `kill -0` is a false-positive trap on WSL2 (and anywhere
# else a port is invisibly held — VS Code remote port forwarding,
# Hyper-V excluded ranges): uvicorn binds AFTER lifespan startup,
# so the process is still alive when the check runs even though
# the bind is about to fail with EADDRINUSE.
#
# `--noproxy '*'` is mandatory: this is a loopback probe, but if the
# user's shell exports http_proxy/ALL_PROXY (e.g. a clash/VPN proxy on
# 127.0.0.1:7890), curl routes even 127.0.0.1 through it — so a proxy
# that is down (or can't loop back to us) yields a false "did not
# become healthy" while the server is actually fine.
HEALTH_URL="http://127.0.0.1:$PORT/api/health"
HEALTHY=false
for _ in $(seq 1 30); do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        break
    fi
    if curl -fsS --noproxy '*' -o /dev/null --max-time 2 "$HEALTH_URL" 2>/dev/null; then
        HEALTHY=true
        break
    fi
    sleep 1
done

if ! $HEALTHY; then
    echo "ERROR: Server did not become healthy on :$PORT."
    echo "Last 20 lines of $LOG_DIR/backend.log:"
    tail -20 "$LOG_DIR/backend.log"
    kill -9 "$BACKEND_PID" 2>/dev/null || true
    exit 1
fi
echo "Server started (PID $BACKEND_PID)"

# Save PID for stop.sh
echo "$BACKEND_PID" > "$SCRIPT_DIR/.pids_$PORT"

echo ""
echo "=========================================="
echo "vibe-seller running on port $PORT"
echo "URL: http://0.0.0.0:$PORT/"
echo "API: http://0.0.0.0:$PORT/api/health"
echo "Logs: $LOG_DIR/"
echo "=========================================="
