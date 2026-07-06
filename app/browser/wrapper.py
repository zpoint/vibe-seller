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
from pathlib import Path
import re
import shutil
import stat
import sys
import textwrap

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
# Boot cleanup (BrowserManager._wipe_generated_wrappers) deletes wrappers
# with a version BELOW this and never touches equal-or-higher ones. So:
#   - a stale pre-0.13 wrapper (v1 / unmarked) is removed on upgrade;
#   - the current version's own wrappers SURVIVE a restart (no wrapper-less
#     window → the agent can't fall through to a local Chrome);
#   - a running vN never nukes a wrapper written by a newer vN+1
#     (rollback / mixed-process safety).
# See docs/browser-use-0.13-migration.md § wrapper-format versioning.
WRAPPER_FORMAT_VERSION = 2
WRAPPER_FORMAT_MARKER = 'vibe-seller-wrapper-format:'


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
    - Injects ``BU_CDP_WS=ws://proxy/client-{task_id}`` (both backends;
      aux on Ziniao is Chrome-direct so it gets no proxy endpoint).
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

    # Prefer the browser-use binary that lives in the same bin/ as the
    # daemon's Python interpreter. This is the ONLY place it exists in
    # `uv tool install vibe-seller` mode — the tool venv's bin is not
    # on PATH, so shutil.which() would fall through to the bare-string
    # fallback and produce a wrapper that fails at exec time with
    # "command not found". In `./start.sh` (dev clone) mode both paths
    # find it; the sibling lookup is just authoritative.
    #
    # Do NOT .resolve() here: uv tool venvs (and `uv venv`) symlink the
    # interpreter to a base Python (pyenv / homebrew / conda). Following
    # that symlink lands in a dir that often has its OWN `browser-use`
    # from a different (typically older) install — picking it up would
    # bypass our pinned `browser-use>=0.13` and produce a wrapper that
    # drives the wrong CLI. 0.13 keeps the `browser-use` entry point, so
    # the sibling path resolves the same after an in-place upgrade.
    daemon_bin = Path(sys.executable).parent
    candidate = daemon_bin / 'browser-use'
    real_bu = (
        str(candidate) if candidate.is_file() else shutil.which('browser-use')
    )
    if not real_bu:
        logger.warning(
            'browser-use binary not found on PATH; '
            'wrapper will use "browser-use" as fallback'
        )
        real_bu = 'browser-use'

    port = api_port or BACKEND_PORT

    assert proxy_port is not None, 'proxy_port required for all backends'
    cdp_http_url = f'http://{LOCALHOST}:{proxy_port}'

    auth_header = ''
    if api_token:
        auth_header = (
            f'\n                  -H "Authorization: Bearer {api_token}" \\'
        )

    # For Ziniao stores, -aux sessions bypass the proxy (Chrome direct).
    # For Chrome stores, ALL sessions go through CDPMuxProxy (no aux).
    if backend == 'ziniao':
        aux_case_open = (
            f'{slug}-aux)\n'
            f'                ;;  # aux session — Chrome direct, no proxy\n'
            f'              '
        )
    else:
        aux_case_open = ''

    auto_start_block = textwrap.dedent(f"""\
        # Auto-start: ensure CDP proxy is responding.
        case "$SESSION" in
          {aux_case_open}{slug}|{slug}-*)
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
    #   BU_CDP_WS  — attach to this task's CDP proxy client (non-aux)
    # aux (Ziniao) is Chrome-direct: no BU_CDP_WS → browser_harness
    # discovers local Chrome. CLIENT_ID mirrors the 0.12 proxy client id
    # so the mux proxy's per-task isolation is unchanged.
    env_inject = textwrap.dedent(f"""\
        export BU_NAME="$SESSION"
        case "$SESSION" in
          {aux_case_open}{slug}|{slug}-*)
            CLIENT_ID="${{VIBE_TASK_ID:-$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')}}"
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
    # On a wedge we reload THIS session's daemon (``browser-use --reload``
    # → browser_harness ``restart_daemon()``, scoped by BU_NAME) and
    # surface the failure. We do NOT auto-retry: unlike the 0.12
    # subcommand CLI, a 0.13 heredoc can mutate the page (click/type), so
    # blindly re-running could double-apply. The agent re-issues on the
    # reported error against a fresh daemon.
    #
    # aux (Ziniao, Chrome-direct) is never self-healed — matches 0.12.
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
        if [ "$SESSION" != "{slug}-aux" ]; then
          set +e
          {run_line}
          _vs_rc=$?
          set -e
          if [ "$_vs_rc" -eq 142 ]; then
            echo "[wrapper] browser-use timed out (120s) — reloading daemon '$SESSION'" >&2
            BU_NAME="$SESSION" "$REAL_BU" --reload >/dev/null 2>&1 || true
          fi
          exit "$_vs_rc"
        fi
    """)

    exec_line = 'exec "$REAL_BU" ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}'

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

        # 0.13 has no subcommands — the agent pipes Python via stdin
        # (heredoc) or -c. We intercept only the isolation-relevant flags
        # and pass everything else (e.g. -c '<code>') straight through.
        PASSTHROUGH=()
        while [ $# -gt 0 ]; do
          case "$1" in
            --session|--session=*)
              case "$1" in
                --session) shift; _REQ_SESSION="${{1:-}}"; shift ;;
                *)         _REQ_SESSION="${{1#--session=}}"; shift ;;
              esac
              if [ -n "${{VIBE_TASK_ID:-}}" ] && [ "$_REQ_SESSION" != "{slug}-aux" ]; then
                echo "ERROR: --session is auto-assigned per task ($SESSION). Only --session {slug}-aux may override in task mode." >&2
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

        # Validate session format: {slug}, {slug}-aux, or {slug}-{{8hex}}
        if [[ ! "$SESSION" =~ ^{slug}(-aux|-[0-9a-fA-F]{{8}})?$ ]]; then
            echo "ERROR: session '$SESSION' not allowed. Allowed: {slug}, {slug}-aux, {slug}-{{8-hex-chars}}" >&2
            exit 1
        fi

    """)

    script += auto_start_block + '\n'
    script += env_inject + '\n'
    # Self-heal path runs first (and exits) for proxy sessions; aux falls
    # through to a plain exec.
    script += selfheal_block + '\n'
    script += exec_line + '\n'

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
