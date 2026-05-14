#!/bin/bash

# Stop vibe-seller server
# Usage: ./stop.sh           # stops port 7777 (default)
#        ./stop.sh 7780     # stops port 7780
#        ./stop.sh --all    # stops all uvicorn app.main:app processes from this project

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = "--all" ]; then
    echo "Stopping all vibe-seller servers..."

    # Only kill uvicorn processes whose working directory is this project
    pids=""
    for pid in $(pgrep -f 'uvicorn app.main:app' 2>/dev/null); do
        pid_cwd=$(lsof -p "$pid" 2>/dev/null | awk '$4=="cwd"{print $9}')
        if [ "$pid_cwd" = "$SCRIPT_DIR" ]; then
            pids="$pids $pid"
        fi
    done

    pids=$(echo "$pids" | xargs)
    if [ -z "$pids" ]; then
        echo "No running servers found."
    else
        echo "Stopping PIDs: $pids..."
        echo "$pids" | xargs kill -9 2>/dev/null
        echo "All servers stopped."
    fi

    # Clean up pid files
    rm -f "$SCRIPT_DIR"/.pids_*
    exit 0
fi

PORT="${1:-7777}"

echo "Stopping server on port $PORT..."

# Kill processes using the port
lsof -ti:"$PORT" 2>/dev/null | xargs kill -9 2>/dev/null

# Also kill by PID file if exists
PID_FILE="$SCRIPT_DIR/.pids_$PORT"
if [ -f "$PID_FILE" ]; then
    cat "$PID_FILE" | xargs kill -9 2>/dev/null
    rm -f "$PID_FILE"
fi

echo "Server on port $PORT stopped."
