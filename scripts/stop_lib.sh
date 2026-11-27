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

# The VIBE_HOME a server pid runs with (read BEFORE it is stopped —
# the environment dies with the process). Empty when it cannot be
# read, which disables home reaping for that pid.
server_vibe_home() {
    local pid="$1"
    if [ -r "/proc/$pid/environ" ]; then
        tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null \
            | sed -n 's/^VIBE_HOME=//p' | head -1
    else
        # macOS: `ps eww` appends the environment to the command line.
        ps eww -o command= -p "$pid" 2>/dev/null \
            | tr ' ' '\n' | sed -n 's/^VIBE_HOME=//p' | head -1
    fi
}

# Reap the leftovers of THIS home: processes whose environment both
# carries this exact VIBE_HOME and marks them as a task agent's
# (``VIBE_TASK_ID`` — every agent-started process inherits it, however
# detached) or a browser-use daemon (``BH_RUNTIME_DIR`` under this
# home). Nothing else is touched: never another instance's (each has
# its own VIBE_HOME), never production's, and never a shell that
# merely exported the same VIBE_HOME without a task marker. The
# server pid itself is excluded (the caller is stopping it). Native
# Windows servers do not run stop.sh; their task leftovers are
# covered by the per-task job objects in app/task_reaper.py.
reap_home_leftovers() {
    local home="$1" self_pid="$2" pid env count=0
    [ -n "$home" ] || return 0
    for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
        [ "$pid" = "$self_pid" ] && continue
        env=$(tr '\0' '\n' 2>/dev/null < "/proc/$pid/environ")
        [ -n "$env" ] || continue
        if printf '%s\n' "$env" | grep -qx "VIBE_HOME=$home"; then
            if printf '%s\n' "$env" | grep -q '^VIBE_TASK_ID=' \
                || printf '%s\n' "$env" \
                    | grep -q "^BH_RUNTIME_DIR=$home/r"; then
                kill -TERM "$pid" 2>/dev/null && count=$((count + 1))
            fi
        fi
    done
    # No /proc (macOS): one ps call carries every environment.
    if [ ! -d /proc ]; then
        for pid in $(ps eww -axo pid= 2>/dev/null); do
            [ "$pid" = "$self_pid" ] && continue
            if ps eww -o command= -p "$pid" 2>/dev/null | tr ' ' '\n' \
                | grep -qx "VIBE_HOME=$home"; then
                if ps eww -o command= -p "$pid" 2>/dev/null | tr ' ' '\n' \
                    | grep -q '^VIBE_TASK_ID=\|^BH_RUNTIME_DIR='"$home"'/r'; then
                    kill -TERM "$pid" 2>/dev/null && count=$((count + 1))
                fi
            fi
        done
    fi
    # Escalate after a short grace, and drop this home's daemon pid
    # files even if their daemons already died.
    sleep 1
    for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
        [ "$pid" = "$self_pid" ] && continue
        env=$(tr '\0' '\n' 2>/dev/null < "/proc/$pid/environ")
        [ -n "$env" ] || continue
        if printf '%s\n' "$env" | grep -qx "VIBE_HOME=$home"; then
            if printf '%s\n' "$env" | grep -q '^VIBE_TASK_ID=' \
                || printf '%s\n' "$env" \
                    | grep -q "^BH_RUNTIME_DIR=$home/r"; then
                kill -KILL "$pid" 2>/dev/null
            fi
        fi
    done
    rm -f "$home"/r/bu-*.pid "$home"/r/bu-*.sock 2>/dev/null
    [ "$count" -gt 0 ] && echo "  reaped $count leftover(s) of VIBE_HOME=$home"
    return 0
}
