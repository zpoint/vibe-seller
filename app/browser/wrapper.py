"""Per-store browser-use wrapper script generation (browser-use 0.13).

Creates ``$VIBE_HOME/bin/{slug}/browser-use`` — a bash wrapper that
assigns a per-task session, blocks agent overrides, injects the CDP mux
proxy endpoint + daemon runtime dir as ENVIRONMENT VARIABLES, auto-starts
the proxy if down, and bounds each call with a wedge-recovery timeout.

browser-use 0.13 removed the subcommand CLI (``open``/``state``/``click``).
The agent now pipes Python helper code via a heredoc/`-c`, and connection
identity moved from flags to env vars:

  * ``BU_NAME``   — the session/daemon name (was ``--session``)
  * ``BU_CDP_WS`` — the CDP websocket to attach to (was ``--cdp-url``)

The daemon (``browser_harness``) records itself as
``bu-<BU_NAME>.pid``/``.sock`` under ``BH_RUNTIME_DIR``; the reaper keys
off those files. See docs/browser-use-0.13-migration.md.
"""

import logging
import os
from pathlib import Path
import re
import stat
import tempfile
import textwrap
import unicodedata

from app.browser.base import is_antidetect
from app.browser.bu_binary import resolve_browser_use
from app.browser.worker_slots import (
    indent_block,
    max_worker_slots,
    worker_flag_arm,
    worker_init_block,
    worker_resolve_block,
    worker_session_regex_fragment,
)
from app.browser.wrapper_files import (  # noqa: F401 — re-export
    WRAPPER_FORMAT_MARKER,
    WRAPPER_FORMAT_VERSION,
    _cleanup_legacy_store_dirs,
    remove_browser_use_wrapper,
    wipe_generated_wrappers,
)
from app.config import (
    BACKEND_PORT,
    BH_RUNTIME_DIR,
    BH_TMP_DIR,
    BROWSER_USE_BIN_DIR,
    DOWNLOADS_DIR,
    LOCALHOST,
)
from app.platform import safe_chmod

logger = logging.getLogger(__name__)

# Directory for per-store browser-use wrapper scripts
_BIN_DIR = BROWSER_USE_BIN_DIR

#: Exit code for "this browser cannot be revived from here" — distinct
#: from 142 (the 120s alarm) so a caller can tell "try again on a fresh
#: daemon" from "stop trying and record the gap". Chosen from the
#: sysexits range (EX_TEMPFAIL) to avoid colliding with 128+signal.
WEDGE_UNRECOVERABLE_RC = 75


# The shape every store slug has (app/store_slugs.py): lowercase alphanumerics
# and hyphens, no leading/trailing/doubled hyphen. The single source
# of truth for what a stores/<slug>/ path segment may contain: routes
# that take a caller-supplied slug validate it against this pattern
# (``validate_store_slug``) instead of trusting the string.
STORE_SLUG_RE = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')


def validate_store_slug(slug: str) -> str:
    """Reject a caller-supplied slug that is not a store-slug shape.

    ``routers/stores.py`` never takes a slug from the caller — it uses
    the stored ``Store.slug`` (app/store_slugs.py), so every on-disk
    store path there is this shape by construction. The workspace
    store-profile route is the one surface where a raw slug string arrives (REST and
    MCP alike), and it flows straight into ``root / 'stores' / slug``:
    an unvalidated ``'/tmp/evil'`` or ``'../../..'`` writes outside
    the workspace. Rejecting everything outside the derived alphabet
    (400 at the route, ToolError at the MCP tool) makes the path
    segment incapable of escaping — there is no ``/``, no ``..``, no
    absolute prefix in ``[a-z0-9-]``.
    """
    if not isinstance(slug, str) or not STORE_SLUG_RE.fullmatch(slug):
        raise ValueError(
            f'Invalid store slug {slug!r}: use lowercase letters, '
            'digits and single hyphens (the shape store names derive '
            'to), e.g. "acme-shop"'
        )
    return slug


def _comment_safe(text: str) -> str:
    """*text* fit for one line of a bash comment.

    A line break would end the comment, and the rest of the line would
    run as a command. Store names are saved as typed, so one can hold a
    line break; the script is where that must stop mattering.
    """
    return ''.join(
        ' ' if ch.isspace() or unicodedata.category(ch) == 'Cc' else ch
        for ch in text
    )


def atomic_write_script(path: Path, script: str) -> None:
    """Write a wrapper script atomically (tmp file + rename).

    bash reads a script lazily from its open file descriptor, so an
    in-place rewrite of a RUNNING wrapper scrambles its execution — and
    rewriting a running wrapper is now a supported flow, not an edge
    case: when the server reallocates the proxy port it rewrites this
    very file while an older invocation of it is mid-auto-start. The
    rename keeps the running invocation on the old inode (consistent
    reads) while the wrapper's port refresh — which re-reads the PATH —
    sees the new content. mkstemp's 0600 keeps the bearer token
    owner-only through the swap.

    Windows cannot rename over a script that is EXECUTING (bash holds
    it; ``os.replace`` raises ``PermissionError`` [WinError 5]) — and
    the wrapper's own auto-start path is exactly that caller: a cold
    store's first ``browser-use`` call asks the server to start the
    browser, the server regenerates this wrapper (usually
    byte-identical), and the rename fails because the caller IS the
    running wrapper. Found live by a real task on native Windows. So:

    - identical content → no write at all (the common path; nothing
      to swap, nothing to lock);
    - a locked target → keep the OLD file and warn: the running
      invocation already follows the typed ``proxy_port`` from the
      start API at runtime, and the next quiet write lands the new
      content.
    """
    try:
        if path.is_file() and path.read_text(encoding='utf-8') == script:
            return
    except OSError:
        pass  # unreadable/missing — fall through and (re)write it
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f'.{path.name}.', suffix='.tmp'
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(script)
        os.replace(tmp, str(path))
    except PermissionError:
        # Windows: the target is executing (see above). The running
        # wrapper self-heals via the start API's typed port; leave the
        # old file in place rather than failing the browser start.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        logger.warning(
            'Wrapper %s is executing (Windows lock) — kept the previous '
            'copy; the running call follows the start API port anyway',
            path,
        )
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_browser_use_wrapper(
    store_name: str,
    backend: str,
    proxy_port: int | None,
    api_port: int | None = None,
    api_token: str | None = None,
    store_id: str | None = None,
    headless: bool = True,
    instance_id: str | None = None,
    *,
    slug: str,
):
    """Generate a per-store bash wrapper around browser-use 0.13.

    Creates ``~/.vibe-seller/bin/{slug}/browser-use`` that:
    - Assigns a per-task session: ``BU_NAME={slug}-{VIBE_TASK_ID[:8]}``
    - Accepts only ``--session {slug}-aux`` as an override (maps to
      ``BU_NAME``); rejects any other session and agent-supplied
      ``BU_NAME``/``BU_CDP_URL``/``BU_CDP_WS``/``--mcp``/``--connect``/
      ``--profile``.
    - Injects an EXPLICIT ``BU_CDP_WS`` for every session (both
      backends): per-task sessions get ``client-{task_id}``, aux gets
      the stable ``client-aux`` — always on this store's own proxy.
      No session is ever left endpoint-less (an unset endpoint falls
      back to browser_harness's ambient Chrome discovery, which can
      attach to a different store's browser).
    - Points ``BH_RUNTIME_DIR``/``BH_TMP_DIR`` at vibe-seller-managed
      dirs (shared, so daemon files carry ``BU_NAME`` for the reaper).
    - Auto-starts the CDP proxy via authenticated API call if down —
      where "down" now includes "held by something that is not THIS
      server instance" (the ``instance_id`` ownership handshake, PR
      C1: the probe requires the proxy's ``/vibe/instance`` answer to
      match, so a foreign server's proxy or an agent-spawned CDP
      bridge never reads as alive).
    - Bounds each call with a hard timeout; on a wedge it reloads the
      session's daemon (``--reload``) and surfaces the error (no blind
      retry — a heredoc may mutate the page, so re-running is unsafe).

    ``headless`` is accepted for signature compatibility but no longer
    used here: 0.13 attaches to the Chrome our backend already launched
    (via the CDP proxy), so window visibility is the backend's concern,
    not a browser-use flag.

    ``instance_id`` is the owning server's CDP-proxy instance id (see
    ``BrowserManager.instance_id``); it is what the liveness probe
    requires from the proxy before attaching.

    ``slug`` is the store's stored ``Store.slug`` — never derived from
    the name here (app/store_slugs.py).
    """
    wrapper_dir = _BIN_DIR / slug
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = wrapper_dir / 'browser-use'

    # An absolute REAL_BU is a correctness requirement, not a nicety —
    # see app/browser/bu_binary.py. Raises rather than emitting a
    # wrapper that would exec itself.
    real_bu = resolve_browser_use()

    port = api_port or BACKEND_PORT

    assert proxy_port is not None, 'proxy_port required for all backends'

    auth_header = ''
    if api_token:
        auth_header = (
            f'\n                  -H "Authorization: Bearer {api_token}" \\'
        )

    # ALL sessions — aux included, both backends — go through the
    # store's own CDPMuxProxy with an explicit BU_CDP_WS. The old
    # Ziniao-aux "Chrome direct" exemption exported NO endpoint and
    # relied on browser_harness's ambient local-Chrome discovery, which
    # attaches to whatever debug port it finds first — observed live:
    # one store's aux daemon landed inside a DIFFERENT store's browser
    # (its proxy owned the default port), driving the wrong Amazon
    # account and dropping downloads in the wrong store's dir. An
    # unset endpoint must be unrepresentable: every allowed session
    # gets its own explicit client on its own store's proxy.

    # Ziniao aux runs its OWN lazy-started browser (see env_inject), so
    # it must not force the MAIN browser up; chrome aux rides the main
    # proxy and therefore does need it.
    if is_antidetect(backend):
        aux_autostart_arm = (
            f'{slug}-aux)\n'
            f'                ;;  # aux: own browser, started in env step\n'
            f'              '
        )
    else:
        aux_autostart_arm = ''

    auto_start_block = textwrap.dedent(f"""\
        # Auto-start: ensure THIS server's CDP proxy is answering.
        # _vs_proxy_ours is an OWNERSHIP probe, not a bare liveness
        # check: the listener on the port must answer /vibe/instance
        # with this server's baked instance id (PR C1). Liveness alone
        # glued this agent onto whatever won the port — another
        # vibe-seller server on the machine, or an agent-spawned CDP
        # bridge. A listener without our id reads as "down": ask the
        # server to start the browser (it reallocates the port when the
        # baked one is foreign) and follow the port it hands back.
        _vs_proxy_ours() {{
          curl -s --max-time 2 "$CDP_HTTP/vibe/instance" 2>/dev/null | grep -qF "\\"$VIBE_PROXY_ID\\"" || return 1
          # Upstream liveness: the mux serves the ownership
          # handshake itself, so a 200 there says nothing about
          # the browser behind it — the mux answers
          # /json/version 502 while its browser is dead, and
          # the start call must still fire (found live: dead
          # browser + live mux = hard failures).
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
              while :; do
                _start_resp=$(curl -s -w '\\n%{{http_code}}' \\
                  -X POST \\{auth_header}
                  -H "Content-Type: application/json" \\
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
                break
              done
              if ! [ "$_start_http" -ge 200 ] 2>/dev/null \\
                 || ! [ "$_start_http" -lt 300 ] 2>/dev/null; then
                echo "ERROR: browser start API failed (HTTP ${{_start_http:-unavailable}})" >&2
                echo "$_start_resp" | head -5 >&2
                exit 1
              fi
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

    # Inject the CDP endpoint + daemon identity as ENV VARS (0.13 model).
    #   BU_NAME    — session/daemon name (reaper keys off bu-<BU_NAME>.pid)
    #   BU_CDP_WS  — always explicit; NO session is ever endpoint-less
    #                (an unset endpoint falls back to ambient Chrome
    #                discovery, which attached to another store's
    #                browser — observed live).
    # Ziniao aux = the store's DEDICATED login-less Chromium: lazily
    # started via the aux/start API, daemon attaches to the returned
    # aux-proxy ws. Chrome aux = a stable extra client on the main
    # proxy. Per-task sessions mirror the 0.12 proxy client id.
    if is_antidetect(backend):
        aux_env_arm = (
            f'{slug}-aux)\n'
            f'            _aux_resp=$(curl -s -X POST \\{auth_header}\n'
            f'              -H "Content-Type: application/json" \\\n'
            f'              --max-time 90 \\\n'
            f'              "http://{LOCALHOST}:{port}/api/stores/'
            f'{store_id or "UNKNOWN"}/browser/aux/start" \\\n'
            f'              2>/dev/null) || true\n'
            # Parse the ``ws`` field with sed, NOT python3: on native
            # Windows ``python3`` resolves to the non-functional
            # WindowsApps execution-alias stub (returns nothing), which
            # left BU_CDP_WS empty and failed every aux start. sed is
            # bundled in git-bash (/usr/bin/sed) and present on Linux/Mac.
            f'            BU_CDP_WS=$(printf \'%s\' "$_aux_resp" | '
            f'sed -n \'s/.*"ws"[[:space:]]*:[[:space:]]*'
            f'"\\([^"]*\\)".*/\\1/p\') || true\n'
            f'            if [ -z "${{BU_CDP_WS:-}}" ]; then\n'
            f'              echo "ERROR: aux browser start failed: '
            f'$(printf \'%s\' "$_aux_resp" | head -c 300)" >&2\n'
            f'              exit 1\n'
            f'            fi\n'
            f'            export BU_CDP_WS\n'
            f'            ;;\n'
            f'          '
        )
    else:
        aux_env_arm = (
            f'{slug}-aux)\n'
            f'            export BU_CDP_WS='
            f'"ws://{LOCALHOST}:$PROXY_PORT/client-aux"\n'
            f'            ;;\n'
            f'          '
        )
    # ``$WORKER_SUFFIX`` is ``-w<N>`` for a --worker call and empty
    # otherwise. It lands on BOTH the daemon name (via $SESSION) and the
    # mux client id, so slot -> (daemon, client) stays bijective: a
    # worker can never end up sharing a tab with the main session or
    # with another slot. See app/browser/worker_slots.py.
    env_inject = textwrap.dedent(f"""\
        export BU_NAME="$SESSION"
        case "$SESSION" in
          {aux_env_arm}{slug}|{slug}-*)
            CLIENT_ID="${{VIBE_TASK_ID:-$(uuidgen 2>/dev/null || echo t$(date +%s)$$-$RANDOM)}}${{WORKER_SUFFIX}}"
            export BU_CDP_WS="ws://{LOCALHOST}:$PROXY_PORT/client-${{CLIENT_ID}}"
            ;;
        esac
    """)

    # Wedge recovery (proxy/non-aux sessions).
    # ----------------------------------------------------------------
    # A renderer can wedge (hang on the CDP handshake) while the browser
    # process stays alive; every subsequent call against that daemon then
    # hangs identically. We bound each call with a hard timeout (perl
    # alarm — macOS has no GNU ``timeout``; the interval timer survives
    # execve and SIGALRM's default action kills the exec'd browser-use).
    #
    # A reload only cures a wedge that lives in the daemon. When the
    # browser itself is the wedged party — renderer resource-exhausted,
    # the site serving a maintenance page — the reload reconnects to the
    # same sick browser and the next call times out identically.
    #
    # Reloading forever is worse than failing: every call looks like a
    # fresh transient timeout, so the caller keeps paying 120s to learn
    # nothing. Observed live: a store spent 20 minutes cycling
    # reload → navigate → timeout and lost 11 of its downloads, while the
    # wrapper reported each round as an ordinary error.
    #
    # So consecutive timeouts are counted, and the second one ends it:
    #
    #   1st  reload THIS session's daemon (``browser-use --reload`` →
    #        browser_harness ``restart_daemon()``, scoped by BU_NAME).
    #   2nd  the reload did not take. Say so and stop: a caller that
    #        keeps retrying past this is burning time on a browser
    #        nothing scoped to this session can revive. An honest gap
    #        beats a silent one — the caller can record the missing work
    #        and let the next run collect it.
    #
    # **The wrapper deliberately stops there instead of recycling the
    # browser.** A store's browser is shared: concurrent tasks (and
    # ``--worker N`` slots) each get their own daemon and mux client on
    # ONE browser, so recycling it is a store-wide, multi-tenant action —
    # it destroys every peer's tabs and login state mid-task. One tenant
    # must not make that call. Worse, it would not even work here:
    # ``browser/start`` only re-launches Ziniao when its control API is
    # not answering, and the manager tears the env down only when the
    # CDP proxy is *dead*. In this wedge both still answer, so the
    # request is a no-op — and in the case where it is
    # not a no-op, every peer's next call escalates the same way and the
    # only thing standing between that and a stop/start storm is the
    # per-store relaunch budget. Recycling a shared browser belongs to
    # the manager, which holds the lock, knows the other tenants, and
    # owns that budget. See ``BrowserManager._start_session_locked``.
    #
    # The counter lives beside the daemon's own pid/sock under
    # BH_RUNTIME_DIR, keyed by session, and is cleared on any non-timeout
    # exit. Per-session and not per-store: one wedged worker slot must
    # not spend another slot's budget — and, since the wrapper no longer
    # touches the shared browser, a slot's wedge stays its own.
    #
    # We never auto-retry the call itself: unlike the 0.12 subcommand
    # CLI, a 0.13 heredoc can mutate the page (click/type), so blindly
    # re-running could double-apply. The agent re-issues on the reported
    # error against a fresh daemon.
    #
    # exec {$ARGV[0]} @ARGV (explicit-program form), NOT bare
    # `exec @ARGV`. With an empty PASSTHROUGH (the primary heredoc
    # usage: `browser-use <<'PY' … PY`) @ARGV holds a single element,
    # and perl's `exec LIST` falls back to `/bin/sh -c` because the
    # Windows $REAL_BU path contains backslashes (a shell
    # metacharacter). sh then strips the backslashes and the exec dies
    # with "command not found". The explicit-program form always uses
    # execvp and never consults the shell — robust for any arg count
    # or path (backslashes, spaces like C:\Program Files\…).
    run_line = (
        'perl -e \'alarm shift; exec {$ARGV[0]} @ARGV\' 120 "$REAL_BU"'
        ' ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}'
    )
    selfheal_block = textwrap.dedent(f"""\
        _vs_wedge_file="$BH_RUNTIME_DIR/wedge-$SESSION.n"
        set +e
        {run_line}
        _vs_rc=$?
        set -e
        if [ "$_vs_rc" -ne 142 ]; then
          # Any answer at all — including an ordinary error — proves the
          # browser is reachable, so the escalation budget resets.
          rm -f "$_vs_wedge_file" 2>/dev/null || true
          exit "$_vs_rc"
        fi
        _vs_n=$(cat "$_vs_wedge_file" 2>/dev/null || echo 0)
        case "$_vs_n" in ''|*[!0-9]*) _vs_n=0 ;; esac
        _vs_n=$((_vs_n + 1))
        mkdir -p "$BH_RUNTIME_DIR" 2>/dev/null || true
        printf '%s' "$_vs_n" > "$_vs_wedge_file" 2>/dev/null || true
        echo "[wrapper] browser-use timed out (120s) on '$SESSION'" \\
             "(consecutive: $_vs_n)" >&2
        if [ "$_vs_n" -ge 2 ]; then
          echo "[wrapper] UNRECOVERABLE: reloading the daemon did not" \\
               "restore '$SESSION'." >&2
          echo "[wrapper] Stop retrying this browser — further calls will" \\
               "time out the same way. Record the work you could not" \\
               "finish as a gap and report it; do not present partial" \\
               "output as complete." >&2
          exit {WEDGE_UNRECOVERABLE_RC}
        fi
        BU_NAME="$SESSION" "$REAL_BU" --reload >/dev/null 2>&1 || true
        exit "$_vs_rc"
    """)

    # Parallel worker slots. The bound is resolved HERE, at generation
    # time, and baked into the script — the agent supplies a slot number
    # and the wrapper decides whether it exists. See worker_slots.py.
    slots = max_worker_slots()
    worker_init = indent_block(worker_init_block(slots), 8)
    worker_arm = indent_block(worker_flag_arm(slots), 12)
    worker_resolve = indent_block(worker_resolve_block(slots), 8)
    worker_re = worker_session_regex_fragment(slots)
    # Group the tail before making it optional: a bare ``-w[1-3]?``
    # would attach the ``?`` to the digit class and make ``-w``
    # MANDATORY. Omitted entirely when slots are off — an empty group
    # ``()?`` is undefined in POSIX ERE.
    worker_tail = f'({worker_re})?' if worker_re else ''
    session_re = f'^{slug}(-aux|(-[0-9a-fA-F]{{8}})?{worker_tail})$'
    session_help = f'{slug}, {slug}-aux, {slug}-{{8-hex-chars}}' + (
        f', + optional -w1..-w{slots} worker tail' if slots else ''
    )

    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Auto-generated browser-use wrapper for store: {_comment_safe(store_name)}
        # {WRAPPER_FORMAT_MARKER} {WRAPPER_FORMAT_VERSION}
        # Do not edit — regenerated on session start.
        set -euo pipefail

        # Strip system proxy env so the daemon's CDP WebSocket goes
        # straight to localhost instead of via ClashX/etc.
        unset http_proxy HTTP_PROXY https_proxy HTTPS_PROXY all_proxy ALL_PROXY

        REAL_BU="{real_bu}"

        # This server's proxy port + instance id (PR C1). The wrapper
        # only ever attaches to a mux that answers /vibe/instance with
        # VIBE_PROXY_ID — a listener of any OTHER server (or an
        # agent-spawned CDP bridge) is not "alive". PROXY_PORT is a
        # variable, not a constant: the server may REALLOCATE the port
        # during auto-start (the baked one turned out foreign) and the
        # start API hands the fresh one back as a typed field.
        PROXY_PORT={proxy_port}
        VIBE_PROXY_ID="{instance_id or ''}"
        CDP_HTTP="http://{LOCALHOST}:$PROXY_PORT"

        # Reject agent attempts to hijack the managed connection: BU_*
        # are injected below by us; a value inherited from the agent's
        # env would repoint the daemon at another store or the cloud.
        for _v in BU_NAME BU_CDP_URL BU_CDP_WS BU_AUTOSPAWN; do
          if [ -n "${{!_v:-}}" ]; then
            echo "ERROR: $_v is managed by the wrapper — do not set it." >&2
            exit 1
          fi
        done
        # Never let a stray cloud key auto-spawn (and bill) a cloud browser.
        unset BROWSER_USE_API_KEY BU_AUTOSPAWN 2>/dev/null || true

        # Isolate browser_harness daemon state under vibe-seller. SHARED=1
        # keeps the BU_NAME in each daemon file (bu-<BU_NAME>.pid/.sock)
        # so the reaper can map daemons back to tasks.
        export BH_RUNTIME_DIR="{BH_RUNTIME_DIR}"
        export BH_RUNTIME_DIR_SHARED=1
        export BH_TMP_DIR="{BH_TMP_DIR}"
        export BH_TMP_DIR_SHARED=1
        # browser_harness prefixes the title of every tab it drives with
        # a horse emoji — a change the page itself can observe, and no
        # person's browser does that. Keep store pages untouched.
        export BH_TAB_MARKER=0

        # Per-task session: each task gets its own daemon to avoid
        # "Session already running with different config".
        if [ -n "${{VIBE_TASK_ID:-}}" ]; then
          SESSION="{slug}-${{VIBE_TASK_ID:0:8}}"
        else
          SESSION="{slug}"
        fi

        # Parallel worker slots (--worker N). Declared before the arg
        # loop so `set -u` holds even when the flag is absent.
        {worker_init}
        # 0.13 has no subcommands — the agent pipes Python via stdin
        # (heredoc) or -c. We intercept only the isolation-relevant flags
        # and pass everything else (e.g. -c '<code>') straight through.
        PASSTHROUGH=()
        while [ $# -gt 0 ]; do
          case "$1" in
            {worker_arm}
            --session|--session=*)
              case "$1" in
                --session) shift; _REQ_SESSION="${{1:-}}"; shift ;;
                *)         _REQ_SESSION="${{1#--session=}}"; shift ;;
              esac
              _SESSION_GIVEN=1
              if [ -n "${{VIBE_TASK_ID:-}}" ] && [ "$_REQ_SESSION" != "{slug}-aux" ]; then
                echo "ERROR: --session is auto-assigned per task ($SESSION). Only --session {slug}-aux may override in task mode; use --worker N for a parallel slot." >&2
                exit 1
              fi
              SESSION="$_REQ_SESSION"
              ;;
            --cdp-url|--cdp-url=*|--cdp-ws|--cdp-ws=*)
              echo "ERROR: the CDP endpoint is managed by the wrapper" >&2
              exit 1
              ;;
            --mcp|--mcp=*)
              echo "ERROR: --mcp is not allowed" >&2
              exit 1
              ;;
            --connect|--connect=*)
              echo "ERROR: --connect is not allowed" >&2
              exit 1
              ;;
            --profile|--profile=*)
              echo "ERROR: --profile is not allowed" >&2
              exit 1
              ;;
            *)
              PASSTHROUGH+=("$1")
              shift
              ;;
          esac
        done

        {worker_resolve}
        # Validate session format: {slug}, {slug}-aux, {slug}-{{8hex}},
        # optionally with a `-w<N>` parallel-worker tail.
        if [[ ! "$SESSION" =~ {session_re} ]]; then
            echo "ERROR: session '$SESSION' not allowed. Allowed: {session_help}" >&2
            exit 1
        fi

        # capture_screenshot() writes a fixed shot.png into BH_TMP_DIR, so
        # one dir for every session handed a task another store's picture.
        export BH_TMP_DIR="{BH_TMP_DIR}/$SESSION"

    """)

    script += auto_start_block + '\n'
    script += env_inject + '\n'
    # Every session (aux included) runs the bounded self-heal path.
    script += selfheal_block + '\n'

    # Atomic (tmp + rename): the server legitimately rewrites a wrapper
    # while an older invocation of it is still running — see
    # atomic_write_script. encoding='utf-8' inside it because the
    # script contains non-ASCII (e.g. '→'); Windows' default cp1252
    # can't encode it.
    atomic_write_script(wrapper_path, script)
    safe_chmod(wrapper_path, stat.S_IRWXU)  # 700 — owner-only (contains token)
    logger.info(
        'Wrote browser-use wrapper: %s (backend=%s, proxy=%s)',
        wrapper_path,
        backend,
        proxy_port,
    )
    _cleanup_legacy_store_dirs(
        store_name,
        slug,
        bin_dir=_BIN_DIR,
        downloads_dir=DOWNLOADS_DIR,
    )
