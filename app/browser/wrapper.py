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
import re
import shutil
import stat
import textwrap

from app.browser.bu_binary import resolve_browser_use
from app.browser.worker_slots import (
    indent_block,
    max_worker_slots,
    worker_flag_arm,
    worker_init_block,
    worker_resolve_block,
    worker_session_regex_fragment,
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

# Monotonic wrapper-format version. BUMP on any BREAKING change to the
# generated wrapper's contract (env vars, CLI shape, PATH assumptions).
#   1 = pre-0.13 (0.12 subcommand CLI: open/click/state)
#   2 = 0.13 heredoc + BU_CDP_WS env-injection (current)
# Boot cleanup (``wipe_generated_wrappers`` below) deletes wrappers
# with a version BELOW this and never touches equal-or-higher ones. So:
#   - a stale pre-0.13 wrapper (v1 / unmarked) is removed on upgrade;
#   - the current version's own wrappers SURVIVE a restart (no wrapper-less
#     window → the agent can't fall through to a local Chrome);
#   - a running vN never nukes a wrapper written by a newer vN+1
#     (rollback / mixed-process safety).
# See docs/browser-use-0.13-migration.md § wrapper-format versioning.
# v3: killed the Ziniao-aux "Chrome direct" exemption — aux now gets an
# explicit BU_CDP_WS on its own store's proxy (client-aux). The old
# no-endpoint aux fell back to browser_harness's ambient local-Chrome
# discovery, which attached to another store's browser (wrong Amazon
# account, wrong downloads dir) — observed live with two Ziniao stores.
# v4: Ziniao-aux is a DEDICATED login-less Chromium (per-store, lazy):
# the aux branch calls /browser/aux/start and attaches to the returned
# aux-proxy ws. Chrome-backend aux stays a client on the main proxy.
# v5: `--worker N` parallel slots. A subagent driving the browser
# CONCURRENTLY with its parent used to inherit VIBE_TASK_ID, land on the
# same daemon (browser_harness serves every socket connection in its own
# unguarded asyncio task) and drive the same single tab. The flag gives
# each concurrent caller its own daemon AND its own mux client on the
# same logged-in browser. Wrappers written by v4 don't know the flag.
# v6: escalating wedge recovery. v5 answered every 120s timeout with the
# same daemon reload, which cannot cure a browser that is itself wedged —
# so the caller saw an unbounded run of identical transient-looking
# errors and kept paying for them. v6 escalates to a forced browser
# recycle and then reports the browser as unrecoverable.
WRAPPER_FORMAT_VERSION = 6
WRAPPER_FORMAT_MARKER = 'vibe-seller-wrapper-format:'

#: Exit code for "this browser cannot be revived from here" — distinct
#: from 142 (the 120s alarm) so a caller can tell "try again on a fresh
#: daemon" from "stop trying and record the gap". Chosen from the
#: sysexits range (EX_TEMPFAIL) to avoid colliding with 128+signal.
WEDGE_UNRECOVERABLE_RC = 75


def store_slug(name: str, store_id: str | None = None) -> str:
    """Slugify a store name for use in paths and sessions.

    Produces an ASCII-only slug because ``BU_NAME`` must match
    ``[A-Za-z0-9_-]{1,64}`` (browser_harness ``_check``). Non-ASCII
    names (Chinese, Japanese, ...) fall back to ``store-<id_prefix>``
    when their ASCII reduction is empty, so sessions, bin wrappers, and
    on-disk directories stay portable. Pure-ASCII names keep the same
    slug they had before this guard was introduced.
    """
    slug = re.sub(r'[^a-zA-Z0-9_-]', '-', name.lower())
    slug = re.sub(r'_', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    if slug:
        return slug
    if not store_id:
        raise ValueError(
            f'store_slug: name {name!r} reduces to an empty ASCII '
            'slug and no store_id was provided for fallback'
        )
    return f'store-{store_id[:8]}'


def write_browser_use_wrapper(
    store_name: str,
    backend: str,
    proxy_port: int | None,
    api_port: int | None = None,
    api_token: str | None = None,
    store_id: str | None = None,
    headless: bool = True,
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
    - Auto-starts the CDP proxy via authenticated API call if down.
    - Bounds each call with a hard timeout; on a wedge it reloads the
      session's daemon (``--reload``) and surfaces the error (no blind
      retry — a heredoc may mutate the page, so re-running is unsafe).

    ``headless`` is accepted for signature compatibility but no longer
    used here: 0.13 attaches to the Chrome our backend already launched
    (via the CDP proxy), so window visibility is the backend's concern,
    not a browser-use flag.
    """
    slug = store_slug(store_name, store_id)
    wrapper_dir = _BIN_DIR / slug
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = wrapper_dir / 'browser-use'

    # An absolute REAL_BU is a correctness requirement, not a nicety —
    # see app/browser/bu_binary.py. Raises rather than emitting a
    # wrapper that would exec itself.
    real_bu = resolve_browser_use()

    port = api_port or BACKEND_PORT

    assert proxy_port is not None, 'proxy_port required for all backends'
    cdp_http_url = f'http://{LOCALHOST}:{proxy_port}'

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
    if backend == 'ziniao':
        aux_autostart_arm = (
            f'{slug}-aux)\n'
            f'                ;;  # aux: own browser, started in env step\n'
            f'              '
        )
    else:
        aux_autostart_arm = ''

    auto_start_block = textwrap.dedent(f"""\
        # Auto-start: ensure CDP proxy is responding.
        case "$SESSION" in
          {aux_autostart_arm}{slug}|{slug}-*)
            if ! curl -sf -o /dev/null \\
                 --max-time 2 "{cdp_http_url}/json/version" \\
                 2>/dev/null; then
              # Attempt to start browser via API
              _start_resp=$(curl -s -w '\\n%{{http_code}}' \\
                -X POST \\{auth_header}
                -H "Content-Type: application/json" \\
                --max-time 90 \\
                "http://{LOCALHOST}:{port}/api/stores/{store_id or 'UNKNOWN'}/browser/start?force=1" \\
                2>/dev/null) || true
              _start_http=${{_start_resp##*$'\\n'}}
              if ! [ "$_start_http" -ge 200 ] 2>/dev/null \\
                 || ! [ "$_start_http" -lt 300 ] 2>/dev/null; then
                echo "ERROR: browser start API failed (HTTP ${{_start_http:-unavailable}})" >&2
                echo "$_start_resp" | head -5 >&2
                exit 1
              fi
              # Poll for CDP proxy readiness
              _n=0
              while [ "$_n" -lt 60 ]; do
                if curl -sf -o /dev/null --max-time 2 \\
                     "{cdp_http_url}/json/version" 2>/dev/null; then
                  break
                fi
                sleep 1
                _n=$((_n + 1))
              done
            fi
            # Final CDP readiness check
            if ! curl -sf -o /dev/null --max-time 2 \\
                 "{cdp_http_url}/json/version" 2>/dev/null; then
              echo "ERROR: CDP proxy at {cdp_http_url} not ready after auto-start" >&2
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
    if backend == 'ziniao':
        aux_env_arm = (
            f'{slug}-aux)\n'
            f'            _aux_resp=$(curl -s -X POST \\{auth_header}\n'
            f'              -H "Content-Type: application/json" \\\n'
            f'              --max-time 90 \\\n'
            f'              "http://{LOCALHOST}:{port}/api/stores/'
            f'{store_id or "UNKNOWN"}/browser/aux/start" \\\n'
            f'              2>/dev/null) || true\n'
            f'            BU_CDP_WS=$(printf \'%s\' "$_aux_resp" | '
            f"python3 -c 'import sys,json;"
            f'print(json.load(sys.stdin).get("ws",""))\' '
            f'2>/dev/null) || true\n'
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
            f'"ws://{LOCALHOST}:{proxy_port}/client-aux"\n'
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
            CLIENT_ID="${{VIBE_TASK_ID:-$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')}}${{WORKER_SUFFIX}}"
            export BU_CDP_WS="ws://{LOCALHOST}:{proxy_port}/client-${{CLIENT_ID}}"
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
    # ``browser/start?force=1`` only re-launches Ziniao when it is in
    # normal (non-WebDriver) mode, and the manager tears the env down
    # only when the CDP proxy is *dead*. In this wedge the proxy still
    # answers, so the request is a no-op — and in the case where it is
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
        # Auto-generated browser-use wrapper for store: {store_name}
        # {WRAPPER_FORMAT_MARKER} {WRAPPER_FORMAT_VERSION}
        # Do not edit — regenerated on session start.
        set -euo pipefail

        # Strip system proxy env so the daemon's CDP WebSocket goes
        # straight to localhost instead of via ClashX/etc.
        unset http_proxy HTTP_PROXY https_proxy HTTPS_PROXY all_proxy ALL_PROXY

        REAL_BU="{real_bu}"

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

    """)

    script += auto_start_block + '\n'
    script += env_inject + '\n'
    # Every session (aux included) runs the bounded self-heal path.
    script += selfheal_block + '\n'

    # encoding='utf-8': the script contains non-ASCII (e.g. '→'); Windows'
    # default cp1252 can't encode it.
    wrapper_path.write_text(script, encoding='utf-8')
    safe_chmod(wrapper_path, stat.S_IRWXU)  # 700 — owner-only (contains token)
    logger.info(
        'Wrote browser-use wrapper: %s (backend=%s, proxy=%s)',
        wrapper_path,
        backend,
        proxy_port,
    )
    _cleanup_legacy_store_dirs(store_name, slug)


def _cleanup_legacy_store_dirs(store_name: str, slug: str) -> None:
    """Remove pre-slug-guard artifacts named after the raw store name.

    Before ``store_slug`` gained the ASCII guard, non-ASCII store names
    produced wrapper/download dirs named after the raw name. The fixed
    slug regenerates everything under the id-fallback dir but the stale
    dirs survive — and mislead agents into watching an empty downloads
    dir while real downloads land in the slug dir. Delete them whenever
    the raw name differs from the active slug.

    The stale wrapper dir is only removed when its ``browser-use`` script
    carries our auto-generation header for this store, so a user-created
    dir that happens to share the name is never touched.
    """
    if store_name == slug:
        return
    if store_name in ('.', '..'):
        return  # would resolve to the base dir itself or its parent
    legacy_bin = _BIN_DIR / store_name
    if legacy_bin.parent != _BIN_DIR:
        return  # name contains path separators — never traverse
    try:
        if legacy_bin.exists() and legacy_bin.samefile(_BIN_DIR / slug):
            # Case-insensitive filesystem (macOS default): the "legacy"
            # dir IS the active slug dir — deleting it would destroy the
            # wrapper we just wrote.
            return
    except OSError:
        return
    legacy_wrapper = legacy_bin / 'browser-use'
    if legacy_wrapper.is_file():
        try:
            head = legacy_wrapper.read_text(errors='replace')[:512]
        except OSError:
            head = ''
        if f'wrapper for store: {store_name}' in head:
            shutil.rmtree(legacy_bin, ignore_errors=True)
            logger.info(
                'Removed stale pre-slug-guard wrapper dir: %s '
                '(active slug: %s)',
                legacy_bin,
                slug,
            )
    legacy_downloads = DOWNLOADS_DIR / store_name
    if legacy_downloads.is_dir():
        slug_downloads = DOWNLOADS_DIR / slug
        try:
            if slug_downloads.exists() and legacy_downloads.samefile(
                slug_downloads
            ):
                return  # case-insensitive FS — same dir as active slug
        except OSError:
            return
        try:
            legacy_downloads.rmdir()  # only succeeds when empty
            logger.info(
                'Removed stale empty downloads dir: %s (active slug: %s)',
                legacy_downloads,
                slug,
            )
        except OSError:
            pass  # non-empty — leave user files alone


def remove_browser_use_wrapper(store_name: str, store_id: str | None = None):
    """Remove the per-store browser-use wrapper directory."""
    slug = store_slug(store_name, store_id)
    wrapper_dir = _BIN_DIR / slug
    if wrapper_dir.exists():
        shutil.rmtree(wrapper_dir)
        logger.info('Removed browser-use wrapper dir: %s', wrapper_dir)


def wipe_generated_wrappers(live_store_ids: set[str] | None = None) -> int:
    """Delete OUTDATED or ORPHANED auto-generated wrappers on boot.

    Two independent reasons to remove a wrapper:

    1. **Outdated format** (in-place-upgrade safety): a wrapper left by an
       OLDER version drives a stale CLI/env contract and would misbehave
       if invoked before the next task launch regenerates it. So we remove
       wrappers whose embedded format version is BELOW the current
       ``WRAPPER_FORMAT_VERSION`` (and unmarked/pre-versioning ones,
       treated as version 0).

    2. **Orphaned** — the store it was generated for no longer exists.
       Version alone never reaps these, so a deleted store's wrapper
       survived forever while holding a FROZEN ``proxy_port``. Ports are
       re-allocated from ``_BASE_PROXY_PORT`` every boot, so that number
       eventually lands on a *live* store; the orphan then finds the port
       already up, skips the start API (so its dead ``store_id`` never
       404s) and exports ``BU_CDP_WS`` straight at another store's
       browser — wrong account, wrong downloads dir. Same cross-store
       hole wrapper v3 closed for aux sessions (docs/browser.md).
       Pass ``live_store_ids`` to enable this check.

    We KEEP current-or-newer wrappers belonging to a live store: they
    survive a restart (no wrapper-less window → no local-Chrome fallback,
    see docs/ziniao-concurrency.md), and a running vN never deletes a
    newer vN+1's (rollback safety). User-created wrappers (no auto-gen
    header) are untouched, and so are non-store wrappers such as
    ``_web``/``_guard`` (no embedded store id — fail safe, keep).
    See docs/browser-use-0.13-migration.md.
    """
    removed = 0
    orphaned = 0
    if not _BIN_DIR.is_dir():
        return 0
    # An EMPTY live set is far more likely "wrong/unloaded DB" than
    # "the user deleted every store", and acting on it would delete the
    # wrappers of stores that are actively in use. Never orphan-reap on
    # no evidence — fall back to version-only.
    if live_store_ids is not None and not live_store_ids:
        live_store_ids = None
    for sub in _BIN_DIR.iterdir():
        wrapper = sub / 'browser-use'
        if not wrapper.is_file():
            continue
        try:
            text = wrapper.read_text(errors='replace')
        except OSError:
            continue
        head = text[:400]
        if 'Auto-generated browser-use wrapper' not in head:
            continue  # user-created wrapper — never touch
        m = re.search(rf'{re.escape(WRAPPER_FORMAT_MARKER)}\s*(\d+)', head)
        version = int(m.group(1)) if m else 0
        stale_format = version < WRAPPER_FORMAT_VERSION
        # Ownership: only judge a wrapper we can actually attribute to a
        # store. No parseable id (e.g. the store-less `_web` wrapper) →
        # keep, so a format change here can never orphan-reap everything.
        is_orphan = False
        if live_store_ids is not None:
            # Don't assume a UUID shape — match whatever the wrapper
            # actually embeds, so a non-UUID id is still attributable.
            owner = re.search(r'/api/stores/([^/"\s]+)/browser/', text)
            if owner and owner.group(1) not in live_store_ids:
                is_orphan = True
        if not stale_format and not is_orphan:
            continue
        try:
            wrapper.unlink()
            removed += 1
            if is_orphan:
                orphaned += 1
                # Drop the now-empty dir so `ls bin/` reflects reality.
                try:
                    sub.rmdir()
                except OSError:
                    pass
        except OSError:
            pass
    if removed:
        logger.info(
            'Boot: wiped %d browser-use wrapper(s) (%d outdated, '
            '%d orphaned — store no longer exists)',
            removed,
            removed - orphaned,
            orphaned,
        )
    return removed
