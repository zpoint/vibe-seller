"""The wrapper's browser-start arm: typed failures + transient retry.

C2's half of the generated wrapper script, split out of
``app/browser/wrapper.py`` (which sits at the repo's file-size
ceiling) and kept in its own module so the C1 lane's probe edits and
this failure-reporting contract cannot collide in one file.

The start API answers a taxonomy failure with HTTP 500 +
``{"detail": {"code", "message", "hint"}}``. The agent must never see
a bare "HTTP 500" — that is what made a task agent treat the failure
as a puzzle and SSH into a production machine (incident 97db66d2):

- TERMINAL codes print the typed line (code + hint + "report this to
  the user and stop") and exit 1;
- TRANSIENT codes (CLIENT_NOT_RUNNING / CDP_UNREACHABLE) get exactly
  ONE retry, then the same typed line;
- a body with NO code (a backend outside the inject taxonomy, e.g.
  Ziniao) prints exactly the pre-C2 output — Ziniao is not
  reclassified;
- the start POST names the task it serves (``X-Vibe-Task``) so the
  server can end that task (the header only ever SELECTS this
  store's own live run — see ``app/browser/start_failure.py``).
"""

import textwrap

from app.config import LOCALHOST

#: Seconds the wrapper waits before its ONE retry of a transient
#: browser-start failure (the client may just be coming up). A module
#: constant so tests can shrink it; the wrapper bakes the value in at
#: generation time.
START_TRANSIENT_RETRY_SLEEP_S = 5

#: The typed-failure reporter, defined as a bash function so the
#: transient-retry arm and the final failure arm share it. Reads the
#: HTTP code in ``$_start_http``, the response body in ``$_start_resp``
#: and the already-extracted taxonomy code in ``$_vs_code``.
START_FAILURE_FN = textwrap.dedent("""\
    # Report a failed browser start. Reads the HTTP code in
    # $_start_http, the response body in $_start_resp and the
    # already-extracted taxonomy code in $_vs_code.
    _vs_start_failed() {
      if [ -z "${_vs_code:-}" ]; then
        # No typed code (a backend outside the inject taxonomy,
        # e.g. Ziniao): exactly the pre-C2 output.
        echo "ERROR: browser start API failed (HTTP ${_start_http:-unavailable})" >&2
        printf '%s\\n' "$_start_resp" | head -5 >&2
        exit 1
      fi
      echo "ERROR: [$_vs_code] The store browser could not be started." >&2
      _vs_hint=$(printf '%s' "$_start_resp" \\
        | sed -n 's/.*"hint"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' \\
        | head -1)
      if [ -n "$_vs_hint" ]; then
        echo "$_vs_hint" >&2
      fi
      printf '%s\\n' "$_start_resp" | head -3 >&2
      echo "This cannot be fixed from inside the task. Report this" \\
        "error to the user as-is (code $_vs_code) and stop; do NOT" \\
        "investigate the browser client, its files or its local" \\
        "API, and do not try to start the browser another way." >&2
      exit 1
    }
""")


def auto_start_block(
    *,
    slug: str,
    aux_autostart_arm: str,
    auth_header: str,
    port: int,
    store_id: str | None,
) -> str:
    """The auto-start arm: ensure THIS server's CDP proxy is answering.

    Folds BOTH contracts into one block:
    - C1's OWNERSHIP probe: the listener on ``$CDP_HTTP`` must answer
      ``/vibe/instance`` with ``$VIBE_PROXY_ID`` (both defined by the
      wrapper body before this block). Liveness alone glued an agent
      onto whatever won the port — another server, or an agent-spawned
      CDP bridge. A listener without our id reads as "down": call the
      start API and follow the typed ``proxy_port`` it hands back (the
      server reallocates a foreign port).
    - C2's typed failure handling (``_vs_start_failed`` above): a
      taxonomy-coded start failure is reported as-is and ends the
      call; TRANSIENT codes get exactly one retry.
    """
    return textwrap.dedent(f"""\\
        # Auto-start: ensure THIS server's CDP proxy is answering.
        # _vs_proxy_ours is an OWNERSHIP probe, not a bare liveness
        # check (PR C1). Liveness alone glued this agent onto whatever
        # won the port — another vibe-seller server on the machine, or
        # an agent-spawned CDP bridge.
        _vs_proxy_ours() {{
          curl -s --max-time 2 "$CDP_HTTP/vibe/instance" 2>/dev/null | grep -qF "\"$VIBE_PROXY_ID\"" || return 1
          # Upstream liveness: the mux serves the ownership handshake
          # itself, so a 200 there says nothing about the browser
          # behind it — the mux answers /json/version 502 while its
          # browser is dead, and the start call must still fire
          # (found live: dead browser + live mux = hard failures).
          curl -sf -o /dev/null --max-time 2 "$CDP_HTTP/json/version" 2>/dev/null
        }}
        case "$SESSION" in
          {aux_autostart_arm}{slug}|{slug}-*)
            if ! _vs_proxy_ours; then
              # Attempt to start browser via API. 503 = busy, or Ziniao
              # still starting (the server relaunches it by itself): the
              # start call is idempotent, so ask again, a bounded number
              # of times.
              _try=0
              _vs_transient=0
              while :; do
                _start_resp=$(curl -s -w '\\n%{{http_code}}' \\
                  -X POST \\{auth_header}
                  -H "Content-Type: application/json" \\
                  -H "X-Vibe-Task: ${{VIBE_TASK_ID:-}}" \\
                  --max-time 90 \\
                  "http://{LOCALHOST}:{port}/api/stores/{store_id or 'UNKNOWN'}/browser/start" \\
                  2>/dev/null) || true
                _start_http=${{_start_resp##*$'\\n'}}
                _try=$((_try + 1))
                if [ "$_start_http" = "503" ] && [ "$_try" -lt 3 ]; then
                  echo "browser start busy (try $_try/3): $(printf '%s' "$_start_resp" | head -1 | head -c 300)" >&2
                  sleep 5
                  continue
                fi
                if [ "$_start_http" -ge 200 ] 2>/dev/null \\
                   && [ "$_start_http" -lt 300 ] 2>/dev/null; then
                  # The server may have REALLOCATED the proxy port
                  # (the baked one was foreign): follow the typed
                  # proxy_port the start API hands back — same
                  # pattern as the aux arm following its ws
                  # endpoint (PR C1).
                  _vs_port=$(printf '%s' "$_start_resp" | sed -n 's/.*"proxy_port"[[:space:]]*:[[:space:]]*\\([0-9][0-9]*\\).*/\\1/p' | head -1)
                  case "$_vs_port" in
                    ''|*[!0-9]*) ;;
                    *) PROXY_PORT="$_vs_port"
                       CDP_HTTP="http://{LOCALHOST}:$PROXY_PORT" ;;
                  esac
                  break
                fi
                # Failed. A TRANSIENT taxonomy code (client just coming
                # up, CDP not answering yet) is worth exactly ONE
                # retry; anything else — and a second transient
                # failure — is reported typed and ends the call.
                _vs_code=$(printf '%s' "$_start_resp" \\
                  | sed -n 's/.*"code"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' \\
                  | head -1)
                case "$_vs_code" in
                  CLIENT_NOT_RUNNING|CDP_UNREACHABLE)
                    if [ "$_vs_transient" -lt 1 ]; then
                      _vs_transient=$((_vs_transient + 1))
                      echo "browser start transient error [$_vs_code]; retrying once" >&2
                      sleep {START_TRANSIENT_RETRY_SLEEP_S}
                      continue
                    fi
                    ;;
                esac
                _vs_start_failed
              done
              # The server may have REALLOCATED the proxy port (the
              # baked one was foreign): follow the typed proxy_port the
              # start API hands back — same pattern as the aux arm
              # following its ws endpoint.
              _vs_port=$(printf '%s' "$_start_resp" | sed -n 's/.*"proxy_port"[[:space:]]*:[[:space:]]*\\([0-9][0-9]*\\).*/\\1/p' | head -1)
              case "$_vs_port" in
                ''|*[!0-9]*) ;;
                *) PROXY_PORT="$_vs_port"
                   CDP_HTTP="http://{LOCALHOST}:$PROXY_PORT" ;;
              esac
              # Poll for the proxy to answer as OURS.
              _n=0
              while [ "$_n" -lt 60 ]; do
                if _vs_proxy_ours; then
                  break
                fi
                sleep 1
                _n=$((_n + 1))
              done
            fi
            # Final readiness check: our instance must answer.
            if ! _vs_proxy_ours; then
              echo "ERROR: CDP proxy at $CDP_HTTP not ready after auto-start (it does not serve this server's instance id)" >&2
              exit 1
            fi
            ;;
        esac
    """)
