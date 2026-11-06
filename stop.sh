#!/bin/bash

# Stop vibe-seller server (and its task agents)
# Usage: ./stop.sh              # stops port 7777 (default)
#        ./stop.sh 7780         # stops port 7780
#        ./stop.sh 7780 --scoped  # TEST/DEV SERVERS: stop only the
#                                 # server on that port and its own
#                                 # children — never anything merely
#                                 # CONNECTED to the port (a local
#                                 # proxy relaying to it gets killed
#                                 # by the default path) and never
#                                 # other instances' agents
#        ./stop.sh --all        # stops all uvicorn app.main:app processes from this project
#
# The default path is broad ON PURPOSE: `lsof -ti:PORT` stops everyone
# attached to the port and `reap_agents` clears leftover task
# agents/daemons machine-wide — that is how a crashed server's orphans
# get cleaned up. On a shared machine that blast radius is wrong for a
# throwaway test/demo/e2e server, so `--scoped` opts out of it (see
# scripts/stop_lib.sh for the selection rules).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Reap any task agents / browser daemons that outlived the server.
#
# A running task is a `claude -p` subprocess tree (claude -> MCP server
# -> skill_cli.daemon -> browser-use). The server's graceful (SIGTERM)
# shutdown killpg's these via agent_manager.stop_all(); but a SIGKILL of
# the server bypasses that, and an orphaned agent then keeps calling
# `browser/start` on the next server, thrashing the shared Ziniao client
# (each relaunch tears down other stores' browsers). So we always reap
# as a backstop. Patterns are specific to vibe-seller's headless agent
# (the `--output-format stream-json --input-format stream-json` combo,
# which interactive Claude Code never uses) and its browser daemons, so
# an unrelated Claude session is never touched.
reap_agents() {
    local agent_pat='output-format stream-json --input-format stream-json'
    # A running wrapper poll-loop: the wrapper path followed by a
    # browser-use subcommand. Requiring the subcommand keeps this from
    # matching an editor merely viewing the wrapper script.
    local wrapper_pat='\.vibe-seller/bin/[^ ]*/browser-use (eval|open|state|click|close|screenshot|type|input|keys|sessions|get|extract|scroll|wait|hover|dblclick|rightclick|select|upload|back)'
    local scan="skill_cli.daemon|$agent_pat|$wrapper_pat"
    local n_before
    n_before=$(pgrep -f "$scan" 2>/dev/null | wc -l | tr -d ' ')
    if [ "${n_before:-0}" -gt 0 ]; then
        echo "Reaping $n_before orphaned task agent(s)/daemon(s)/wrapper(s)..."
        # Kill the agent's whole process group so its subtree goes too.
        for pid in $(pgrep -f "$agent_pat" 2>/dev/null); do
            kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
        done
        pkill -TERM -f 'skill_cli.daemon' 2>/dev/null
        pkill -TERM -f "$wrapper_pat" 2>/dev/null
        sleep 2
        for pid in $(pgrep -f "$agent_pat" 2>/dev/null); do
            kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
        done
        pkill -KILL -f 'skill_cli.daemon' 2>/dev/null
        pkill -KILL -f "$wrapper_pat" 2>/dev/null
        local n_after
        n_after=$(pgrep -f "$scan" 2>/dev/null | wc -l | tr -d ' ')
        echo "  task agents/daemons/wrappers remaining: ${n_after:-0}"
    fi
}

# Stop a server PID gracefully (SIGTERM -> lifespan shutdown reaps its
# own agents), escalating to SIGKILL only if it does not exit in time.
stop_pid_graceful() {
    local pid="$1"
    kill -TERM "$pid" 2>/dev/null
    for _ in $(seq 1 20); do   # up to ~10s
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 0.5
    done
    echo "  PID $pid did not exit on SIGTERM; sending SIGKILL"
    kill -9 "$pid" 2>/dev/null
}

# --scoped: stop the selected server(s) and only THEIR own children.
# Selection + reaping live in scripts/stop_lib.sh (testable there).
stop_scoped() {
    . "$SCRIPT_DIR/scripts/stop_lib.sh"
    local pid found=0
    local snap
    snap="$(mktemp)"
    for pid in $(scoped_server_pids_for_port "$1"); do
        found=1
        snapshot_process_tree "$pid" "$snap"
        stop_pid_graceful "$pid"
        reap_tree_snapshot "$snap"
    done
    rm -f "$snap"
    if [ "$found" = 0 ]; then
        echo "  no vibe-seller server (uvicorn app.main:app) on port $1 — nothing stopped"
    fi
}

# Args: first non-flag is the port; --all and --scoped are flags.
PORT=""
ALL=false
SCOPED=false
for arg in "$@"; do
    case "$arg" in
        --all) ALL=true ;;
        --scoped) SCOPED=true ;;
        *) if [ -z "$PORT" ]; then PORT="$arg"; fi ;;
    esac
done
PORT="${PORT:-7777}"

if [ "$ALL" = true ]; then
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
        if [ "$SCOPED" = true ]; then
            . "$SCRIPT_DIR/scripts/stop_lib.sh"
            snap="$(mktemp)"
            echo "Stopping PIDs (scoped): $pids..."
            for pid in $pids; do
                snapshot_process_tree "$pid" "$snap"
                stop_pid_graceful "$pid"
                reap_tree_snapshot "$snap"
            done
            rm -f "$snap"
            echo "All servers stopped (scoped)."
        else
            echo "Stopping PIDs: $pids..."
            for pid in $pids; do
                stop_pid_graceful "$pid"
            done
            echo "All servers stopped."
        fi
    fi

    if [ "$SCOPED" = true ]; then
        :   # each server's own children were reaped above
    else
        reap_agents
    fi

    # Clean up pid files
    rm -f "$SCRIPT_DIR"/.pids_*
    exit 0
fi

echo "Stopping server on port $PORT..."

if [ "$SCOPED" = true ]; then
    stop_scoped "$PORT"
    rm -f "$SCRIPT_DIR/.pids_$PORT"
    echo "Server on port $PORT stopped (scoped)."
    exit 0
fi

# Gracefully stop processes using the port (SIGTERM first so the
# server's shutdown handler reaps its task agents), then SIGKILL.
for pid in $(lsof -ti:"$PORT" 2>/dev/null); do
    stop_pid_graceful "$pid"
done

# Also stop by PID file if it exists
PID_FILE="$SCRIPT_DIR/.pids_$PORT"
if [ -f "$PID_FILE" ]; then
    for pid in $(cat "$PID_FILE"); do
        stop_pid_graceful "$pid"
    done
    rm -f "$PID_FILE"
fi

reap_agents

echo "Server on port $PORT stopped."
