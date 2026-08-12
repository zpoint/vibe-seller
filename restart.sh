#!/bin/bash

# Restart vibe-seller server
# Usage: ./restart.sh           # restart on port 7777 (default)
#        ./restart.sh 7780     # restart on port 7780
#        ./restart.sh --dev    # dev mode (DEBUG logs + AGENT_DEBUG)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Parse args: extract port (skip flags)
PORT=""
EXTRA_ARGS=()
for arg in "$@"; do
    if [ "$arg" = "--dev" ]; then
        EXTRA_ARGS+=("$arg")
    elif [ -z "$PORT" ]; then
        PORT="$arg"
    fi
done
PORT="${PORT:-7777}"

# Pre-flight the SAME prerequisite check start.sh runs, BEFORE stopping.
# start.sh aborts on a missing tool, and stopping first meant a failed
# check took the server down and left it down — a restart that cannot
# start must not be a restart that stops. Observed on macOS over a
# non-interactive `ssh host './restart.sh --dev'`: PATH lacked
# /opt/homebrew/bin, uv/node/pnpm read as missing, and the running
# server was killed before anything noticed. Bail out here with the old
# process still serving.
if ! "$SCRIPT_DIR/install.sh" --check-only >/dev/null 2>&1; then
    echo "======================================" >&2
    echo "Prerequisite check FAILED — server left running, nothing stopped." >&2
    echo "======================================" >&2
    # Re-run visibly so the user sees which tool is missing.
    "$SCRIPT_DIR/install.sh" --check-only || true
    echo "" >&2
    echo "Error: fix the above, then re-run $0" >&2
    exit 1
fi

# Stop the server
echo "======================================"
echo "Step 1: Stopping server..."
echo "======================================"
"$SCRIPT_DIR/stop.sh" "$PORT"
echo ""

# Start the server
echo "======================================"
echo "Step 2: Starting server..."
echo "======================================"
"$SCRIPT_DIR/start.sh" "${EXTRA_ARGS[@]}" "$PORT"
