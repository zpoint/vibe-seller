# Helpers for stop.sh's --scoped mode. Sourced by stop.sh and called
# directly by tests/unit/test_stop_sh.py; never executed on its own.
#
# The invariant --scoped pins: on a port, only THIS project's uvicorn
# server (the process LISTENing there with our command line) and that
# server's own descendants may be signalled. A client merely connected
# to the port — a local proxy relaying to it, an editor, anything — and
# every unrelated process on the machine are out of bounds.

# Repo root (the directory that holds .pids_<port>, one up from here).
STOP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# True when PID is running this project's server command line.
# Substring match on `uvicorn app.main:app`: a reused PID (or some
# other listener on the port) never qualifies.
is_project_uvicorn() {
    local pid="$1" cmd
    cmd=$(ps -o command= -p "$pid" 2>/dev/null)
    [ -n "$cmd" ] || return 1
    case "$cmd" in
        *"uvicorn app.main:app"*) return 0 ;;
    esac
    return 1
}

# Print the PIDs stop.sh --scoped may signal for the given port: the
# project's uvicorn LISTENing on it (never a client of the port) and/or
# the one recorded in .pids_<port> — each re-checked against the
# command line so a stale PID file cannot name a stranger.
scoped_server_pids_for_port() {
    local port="$1" pid seen=" " out=""
    for pid in $(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null); do
        if is_project_uvicorn "$pid"; then
            out="$out $pid"
            seen="$seen$pid "
        fi
    done
    if [ -f "$STOP_LIB_DIR/.pids_$port" ]; then
        for pid in $(cat "$STOP_LIB_DIR/.pids_$port"); do
            case "$seen" in *" $pid "*) continue ;; esac
            if is_project_uvicorn "$pid"; then
                out="$out $pid"
                seen="$seen$pid "
            fi
        done
    fi
    out=$(echo "$out" | xargs)
    [ -n "$out" ] && echo "$out"
    return 0
}

# Print every descendant PID of $1 (recursive `pgrep -P`).
descendant_pids() {
    local pid="$1" child
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        echo "$child"
        descendant_pids "$child"
    done
    return 0
}

# Write "PID<TAB>COMMAND" for $1 and its descendants to file $2.
# The command line is what the reaper re-checks later, so a PID reused
# by another process after the snapshot is never signalled.
snapshot_process_tree() {
    local pid="$1" out="$2" p
    : > "$out"
    for p in $pid $(descendant_pids "$pid"); do
        printf '%s\t%s\n' "$p" "$(ps -o command= -p "$p" 2>/dev/null)" >> "$out"
    done
}

# SIGTERM, then SIGKILL, only the snapshot survivors whose command
# line still matches what we recorded. Reparented orphans (they may
# now belong to launchd/init) are caught by PID from the snapshot;
# anything else on the machine is untouched.
reap_tree_snapshot() {
    local snap="$1" pid cmd live victims=""
    while IFS="$(printf '\t')" read -r pid cmd; do
        [ -n "$pid" ] || continue
        live=$(ps -o command= -p "$pid" 2>/dev/null)
        [ -n "$live" ] || continue
        [ "$live" = "$cmd" ] || continue
        victims="$victims $pid"
    done < "$snap"
    victims=$(echo "$victims" | xargs)
    [ -z "$victims" ] && return 0
    kill -TERM $victims 2>/dev/null
    sleep 1
    victims=""
    while IFS="$(printf '\t')" read -r pid cmd; do
        [ -n "$pid" ] || continue
        live=$(ps -o command= -p "$pid" 2>/dev/null)
        [ -n "$live" ] || continue
        [ "$live" = "$cmd" ] || continue
        victims="$victims $pid"
    done < "$snap"
    victims=$(echo "$victims" | xargs)
    [ -z "$victims" ] && return 0
    kill -KILL $victims 2>/dev/null
    return 0
}
