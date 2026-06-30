"""Per-store browser-use wrapper script generation.

Creates ``$VIBE_HOME/bin/{slug}/browser-use`` — a bash wrapper
that assigns per-task sessions, blocks dangerous flags, injects the
CDP mux proxy URL, and auto-starts the proxy if down.
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
    BROWSER_USE_BIN_DIR,
    DOWNLOADS_DIR,
    LOCALHOST,
)
from app.platform import safe_chmod

logger = logging.getLogger(__name__)

# Directory for per-store browser-use wrapper scripts
_BIN_DIR = BROWSER_USE_BIN_DIR


def store_slug(name: str, store_id: str | None = None) -> str:
    """Slugify a store name for use in paths and sessions.

    Produces an ASCII-only slug because browser-use's CLI rejects
    any ``--session`` name that contains characters outside
    ``[A-Za-z0-9_-]`` (``validate_session_name``). Non-ASCII names
    (Chinese, Japanese, ...) fall back to ``store-<id_prefix>`` when
    their ASCII reduction is empty, so sessions, bin wrappers, and
    on-disk directories stay portable. Pure-ASCII names keep the
    same slug they had before this guard was introduced.
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
    """Generate a per-store bash wrapper around browser-use.

    Creates ``~/.vibe-seller/bin/{slug}/browser-use`` that:
    - Assigns a per-task session name: ``{slug}-{VIBE_TASK_ID[:8]}``
    - Validates --session starts with the store slug
    - Blocks --cdp-url, --mcp, --connect, and --profile flags
    - Injects ``--cdp-url ws://proxy/client-{task_id}`` (both backends)
    - Auto-starts the CDP proxy via authenticated API call if down
    - Injects ``--headed`` when ``headless=False`` so browser-use
      spawns a visible Chrome (the BROWSER_USE_HEADLESS env var is
      cached in the daemon's pydantic config and doesn't take effect
      mid-run; the CLI flag is the reliable knob).
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
    # bypass our pinned `browser-use>=0.12.5` and produce a wrapper that
    # passes flags the older binary doesn't understand (--cdp-url, etc).
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

    # Build the bash script — both Chrome and Ziniao use CDPMuxProxy.
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

    # Probe + recycle for aux sessions — only if a daemon already exists.
    if backend == 'ziniao':
        aux_probe_block = textwrap.dedent(f"""\
            # Probe + recycle for aux session — only if a daemon is
            # already up (else let exec lazy-spawn a fresh one).
            if [ "$SESSION" = "{slug}-aux" ]; then
              _aux_skip=0
              for _arg in "${{PASSTHROUGH[@]}}"; do
                case "$_arg" in
                  close|sessions|shutdown) _aux_skip=1; break ;;
                esac
              done
              if [ "$_aux_skip" = "0" ] \\
                 && "$REAL_BU" sessions 2>/dev/null \\
                    | grep -qE "^{slug}-aux[[:space:]]"; then
                # macOS lacks GNU timeout; use perl alarm as fallback.
                if ! perl -e 'alarm 10; exec @ARGV' \\
                       "$REAL_BU" --session "$SESSION" \\
                       state >/dev/null 2>&1; then
                  "$REAL_BU" --session "$SESSION" close \\
                    >/dev/null 2>&1 || true
                fi
              fi
            fi
        """)
    else:
        aux_probe_block = ''

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
    cdp_inject = textwrap.dedent(f"""\
        # Inject --cdp-url so each task connects via CDPMuxProxy.
        CDP_ARGS=()
        case "$SESSION" in
          {aux_case_open}{slug}|{slug}-*)
            CLIENT_ID="${{VIBE_TASK_ID:-$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')}}"
            CDP_ARGS+=("--cdp-url" "ws://{LOCALHOST}:{proxy_port}/client-${{CLIENT_ID}}")
            ;;
        esac
    """)
    # ${CDP_ARGS[@]+...} avoids "unbound variable" on
    # bash 3.x (macOS default) when array is empty.
    headed_flag = '--headed ' if not headless else ''
    exec_line = (
        f'exec "$REAL_BU" {headed_flag}--session "$SESSION"'
        ' ${CDP_ARGS[@]+"${CDP_ARGS[@]}"}'
        ' "${PASSTHROUGH[@]}"'
    )

    # Self-healing for proxy-backed sessions.
    # ----------------------------------------------------------------
    # A page renderer can wedge (hang on the CDP startup handshake) while
    # the browser process stays alive — the daemon's ``BrowserStartEvent``
    # then times out and EVERY command the agent issues against that
    # daemon hangs or fails identically, with no recovery path. This is
    # what stranded one store’s run: ``open`` looped 6+ times, and once
    # an ``eval``/``state`` hung the harness backgrounded the command and
    # the agent fought a wedged daemon for dozens of steps.
    #
    # We bound every proxy-session command with a hard timeout (perl
    # alarm; macOS has no GNU ``timeout``) and recover on a wedge. The
    # recovery + retry policy differs by command class, because retrying
    # is only safe when the command is idempotent:
    #
    #   nav  (open|navigate) : kill THIS session's daemon + close the
    #                          wedged tab via the proxy, then retry once.
    #                          Navigation is idempotent and ``open`` must
    #                          be a one-shot that always succeeds.
    #   read (state|get)     : kill the daemon (do NOT reset the tab — the
    #                          agent wants to read the CURRENT page), then
    #                          retry once. Pure reads have no side effects.
    #   eval                 : kill the daemon, and retry ONLY when the
    #                          failure proves the daemon never connected
    #                          (so the JS never ran) — agent ``eval``s
    #                          routinely mutate the DOM (click/set value),
    #                          so a blind retry could double-apply. A bare
    #                          alarm-timeout is ambiguous → recover but do
    #                          NOT retry.
    #   other (click|type|…) : plain exec, untouched. Mutating and unsafe
    #                          to retry; a wedge here surfaces on the next
    #                          read/open, which self-heals.
    #
    # The daemon kill is always scoped to the unique per-task session
    # token, so it can never touch a sibling task's daemon. ``-aux`` is
    # Chrome-direct (no proxy) and is never self-healed.
    selfheal_block = textwrap.dedent(f"""\
        # First non-flag token = browser-use subcommand.
        _vs_cmd=""
        for _vs_tok in ${{PASSTHROUGH[@]+"${{PASSTHROUGH[@]}}"}}; do
          case "$_vs_tok" in -*) ;; *) _vs_cmd="$_vs_tok"; break ;; esac
        done

        # Map subcommand → self-heal policy (proxy/non-aux sessions only).
        _vs_policy="none"
        if [ "$SESSION" != "{slug}-aux" ]; then
          case "$_vs_cmd" in
            open|navigate) _vs_policy="nav" ;;
            state|get)     _vs_policy="read" ;;
            eval)          _vs_policy="eval" ;;
          esac
        fi

        if [ "$_vs_policy" != "none" ]; then
          _vs_run() {{
            perl -e 'alarm shift; exec @ARGV' 60 \\
              "$REAL_BU" {headed_flag}--session "$SESSION" \\
              ${{CDP_ARGS[@]+"${{CDP_ARGS[@]}}"}} "${{PASSTHROUGH[@]}}" 2>&1
          }}
          set +e
          _vs_out="$(_vs_run)"
          _vs_rc=$?
          set -e
          if [ "$_vs_rc" -ne 0 ]; then
            # connfail = the daemon could not connect/start, so any JS in
            # an eval never ran (safe to retry). alarm = our 60s hard kill
            # (ambiguous for eval).
            _vs_connfail=0
            printf '%s' "$_vs_out" | grep -qiE \\
              'BrowserStartEvent.*timed out|connect\\(\\) timed out|CDP connection to ws.*(too slow|unresponsive)|Client is stopping|Browser.*not.*(start|connect)' \\
              && _vs_connfail=1
            _vs_alarm=0; [ "$_vs_rc" -eq 142 ] && _vs_alarm=1
            if [ "$_vs_connfail" = "1" ] || [ "$_vs_alarm" = "1" ]; then
              echo "[wrapper] '$_vs_cmd' wedged (rc=$_vs_rc, policy=$_vs_policy) — recovering daemon" >&2
              # Scoped to this task's session token — never a sibling.
              # browser-use traps SIGTERM and hangs when its CDP socket
              # is dead, so -9.
              pkill -9 -f "browser_use.skill_cli.daemon.*$SESSION" 2>/dev/null || true
              # Only nav closes the wedged tab — a read/eval wants the
              # CURRENT page preserved. Scoped to OUR client id (the
              # daemon connects as /client-$CLIENT_ID): the proxy only
              # closes tabs we own or true orphans, never a sibling
              # task's tabs.
              if [ "$_vs_policy" = "nav" ]; then
                curl -sf -o /dev/null --max-time 15 \\
                  "{cdp_http_url}/vibe/reset-tabs?client=${{CLIENT_ID:-}}" 2>/dev/null || true
              fi
              # Retry policy: nav/read always (idempotent); eval only when
              # the JS provably never ran (connfail, not a bare alarm).
              _vs_retry=0
              case "$_vs_policy" in
                nav|read) _vs_retry=1 ;;
                eval)     [ "$_vs_connfail" = "1" ] && _vs_retry=1 ;;
              esac
              if [ "$_vs_retry" = "1" ]; then
                echo "[wrapper] retrying '$_vs_cmd' once" >&2
                sleep 2
                set +e
                _vs_out="$(_vs_run)"
                _vs_rc=$?
                set -e
              fi
            fi
          fi
          printf '%s\\n' "$_vs_out"
          exit "$_vs_rc"
        fi
    """)

    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Auto-generated browser-use wrapper for store: {store_name}
        # Do not edit — regenerated on session start.
        set -euo pipefail

        # Strip system proxy env so the daemon's CDP WebSocket goes
        # straight to localhost:9222 instead of via ClashX/etc.
        unset http_proxy HTTP_PROXY https_proxy HTTPS_PROXY all_proxy ALL_PROXY

        REAL_BU="{real_bu}"

        # Per-task session: each task gets its own daemon to
        # avoid "Session already running with different config".
        if [ -n "${{VIBE_TASK_ID:-}}" ]; then
          SESSION="{slug}-${{VIBE_TASK_ID:0:8}}"
        else
          SESSION="{slug}"
        fi

        PASSTHROUGH=()
        while [ $# -gt 0 ]; do
          case "$1" in
            --session|--session=*)
              # Parse the requested session value first.
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
            --cdp-url|--cdp-url=*)
              echo "ERROR: --cdp-url is managed by the wrapper" >&2
              exit 1
              ;;
            --headed|--headed=*)
              echo "ERROR: --headed is managed by the wrapper" >&2
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
        # Use bash regex for stricter validation (case insensitive hex)
        if [[ ! "$SESSION" =~ ^{slug}(-aux|-[0-9a-fA-F]{{8}})?$ ]]; then
            echo "ERROR: session '$SESSION' not allowed. Allowed: {slug}, {slug}-aux, {slug}-{{8-hex-chars}}" >&2
            exit 1
        fi

        # URL-shape validation for `open` subcommand.
        # ----------------------------------------------------------------
        # Some seller-platform URLs include unquoted `?` and `&`. When
        # an agent invokes them via `browser-use open <URL>` without
        # quotes, the CALLING shell (zsh on macOS) parses `?...` as a
        # failing glob and `&` as a background operator BEFORE this
        # wrapper runs. browser-use then sees a truncated URL (or
        # none at all) and navigation silently falls back to the
        # previous page — the agent thinks the open succeeded because
        # a follow-up `state` returns a non-error page.
        #
        # We can't fix the calling shell from inside this wrapper —
        # the shell parsing happens before our script runs. What we
        # CAN do is detect the shape that proves shell-mangling
        # happened (open without a proper http(s) URL) and exit
        # loudly so the agent sees the failure on the first call
        # instead of compounding it with retries.
        _bu_subcmd=""
        _bu_url=""
        for ((i=0; i<${{#PASSTHROUGH[@]}}; i++)); do
          case "${{PASSTHROUGH[$i]}}" in
            open|navigate)
              _bu_subcmd="${{PASSTHROUGH[$i]}}"
              _bu_url="${{PASSTHROUGH[$((i+1))]:-}}"
              break
              ;;
          esac
        done
        if [ -n "$_bu_subcmd" ]; then
          case "$_bu_url" in
            # Real navigations
            http://*|https://*) : ;;
            # Non-http schemes browser-use uses for recovery:
            # ``about:blank`` resets the active tab; ``file://``
            # opens local artifacts the agent generated. Both
            # are legitimate targets the guard must allow.
            about:*|file://*)   : ;;
            *)
              echo "ERROR: 'browser-use $_bu_subcmd' expects an http(s)://, about:, or file:// URL." >&2
              echo "       Got: ${{_bu_url:-<missing>}}" >&2
              echo "" >&2
              echo "Likely cause: the calling shell (zsh/bash) parsed special" >&2
              echo "characters in your URL before the wrapper saw it. URLs" >&2
              echo "containing '?', '&', or '#' MUST be quoted, e.g.:" >&2
              echo "  browser-use open 'https://example.com/page?a=1&b=2'" >&2
              exit 2
              ;;
          esac
        fi

    """)

    if auto_start_block:
        script += auto_start_block + '\n'
    if aux_probe_block:
        script += aux_probe_block + '\n'
    if cdp_inject:
        script += cdp_inject + '\n'

    # Self-healing path runs first (and exits) for nav/read/eval on
    # proxy sessions; otherwise fall through to a plain exec.
    script += selfheal_block + '\n'
    script += exec_line + '\n'

    wrapper_path.write_text(script)
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

    Before ``store_slug`` gained the ASCII guard, non-ASCII store
    names (e.g. Chinese) produced wrapper/download dirs named after
    the raw name (``bin/云帆科技/``). The fixed slug regenerates
    everything under the id-fallback dir (``bin/store-<id8>/``) but
    the stale dirs survive — and mislead agents into watching an
    empty downloads dir while real downloads land in the slug dir.
    Delete them whenever the raw name differs from the active slug.

    The stale wrapper dir is only removed when its ``browser-use``
    script carries our auto-generation header for this store, so a
    user-created dir that happens to share the name is never touched.
    The stale downloads dir is only removed when empty.
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
            # dir IS the active slug dir — deleting it would destroy
            # the wrapper we just wrote.
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
