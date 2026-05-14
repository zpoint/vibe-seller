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
