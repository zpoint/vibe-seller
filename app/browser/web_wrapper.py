"""Store-less orchestrator "web" browser-use wrapper generation (0.13).

Generates ``$VIBE_HOME/bin/_web/browser-use`` — the wrapper for no-store
(orchestrator) tasks. Split out of ``wrapper.py`` (per-store wrapper) so
each stays a focused, readable module; keep the shared env-injection +
wedge-recovery logic in sync between the two.

Like the per-store wrapper, this targets browser-use 0.13: connection
identity is injected via ``BU_NAME``/``BU_CDP_WS`` env vars (not flags),
and the agent drives the browser by piping Python via a heredoc/`-c`.
"""

import logging
from pathlib import Path
import shutil
import stat
import sys
import textwrap

from app.config import (
    BACKEND_PORT,
    BH_RUNTIME_DIR,
    BH_TMP_DIR,
    BROWSER_USE_BIN_DIR,
    LOCALHOST,
    WEB_BROWSER_SLUG,
)
from app.platform import safe_chmod

logger = logging.getLogger(__name__)

# Directory for browser-use wrapper scripts (shared with wrapper.py).
_BIN_DIR = BROWSER_USE_BIN_DIR


def write_web_browser_use_wrapper(
    proxy_port: int,
    api_port: int | None = None,
    api_token: str | None = None,
    headless: bool = True,
) -> None:
    """Generate the orchestrator "web" browser-use wrapper (0.13).

    Creates ``~/.vibe-seller/bin/_web/browser-use`` for no-store
    (orchestrator) tasks. Unlike the per-store wrapper this browser is
    NOT tied to any store: it's a single generic Chrome session for
    neutral public web work (search, tracking/logistics pages,
    research). It is Chrome-only — no Ziniao routing and no
    ``{slug}-aux`` split — so this is a deliberately simpler sibling of
    :func:`write_browser_use_wrapper`.

    Session naming: ``web`` (interactive) or ``web-{VIBE_TASK_ID[:8]}``
    (per task, so concurrent orchestrator tasks get isolated daemons,
    multiplexed through CDPMuxProxy by ``VIBE_TASK_ID``). Auto-start
    hits ``POST /api/browser/web/start`` (no store id).

    ``headless`` is accepted for signature compatibility but unused: 0.13
    attaches to the Chrome our backend launched (via the CDP proxy), so
    window visibility is the backend's concern, not a browser-use flag.
    """
    wrapper_dir = _BIN_DIR / WEB_BROWSER_SLUG
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = wrapper_dir / 'browser-use'

    # Same binary-resolution rationale as write_browser_use_wrapper:
    # prefer the browser-use next to the daemon interpreter, never
    # .resolve() (uv venvs symlink to a base Python with its own,
    # possibly older, browser-use). 0.13 keeps the `browser-use` entry
    # point, so this path resolves the same after an in-place upgrade.
    daemon_bin = Path(sys.executable).parent
    candidate = daemon_bin / 'browser-use'
    real_bu = (
        str(candidate) if candidate.is_file() else shutil.which('browser-use')
    )
    if not real_bu:
        logger.warning(
            'browser-use binary not found on PATH; '
            'web wrapper will use "browser-use" as fallback'
        )
        real_bu = 'browser-use'

    port = api_port or BACKEND_PORT
    cdp_http_url = f'http://{LOCALHOST}:{proxy_port}'

    auth_header = ''
    if api_token:
        auth_header = (
            f'\n                  -H "Authorization: Bearer {api_token}" \\'
        )

    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Auto-generated browser-use wrapper for the orchestrator web
        # browser (store-less). Do not edit — regenerated on task start.
        set -euo pipefail

        # Strip system proxy env so the daemon's CDP WebSocket goes
        # straight to localhost instead of via ClashX/etc.
        unset http_proxy HTTP_PROXY https_proxy HTTPS_PROXY all_proxy ALL_PROXY

        REAL_BU="{real_bu}"

        # Reject agent attempts to hijack the managed connection.
        for _v in BU_NAME BU_CDP_URL BU_CDP_WS BU_AUTOSPAWN; do
          if [ -n "${{!_v:-}}" ]; then
            echo "ERROR: $_v is managed by the wrapper — do not set it." >&2
            exit 1
          fi
        done
        unset BROWSER_USE_API_KEY BU_AUTOSPAWN 2>/dev/null || true

        # Isolate browser_harness daemon state under vibe-seller (SHARED so
        # each daemon file carries its BU_NAME for the reaper).
        export BH_RUNTIME_DIR="{BH_RUNTIME_DIR}"
        export BH_RUNTIME_DIR_SHARED=1
        export BH_TMP_DIR="{BH_TMP_DIR}"
        export BH_TMP_DIR_SHARED=1

        # Per-task session: each task gets its own daemon so concurrent
        # orchestrator tasks never collide on one session config.
        if [ -n "${{VIBE_TASK_ID:-}}" ]; then
          SESSION="web-${{VIBE_TASK_ID:0:8}}"
        else
          SESSION="web"
        fi

        # 0.13 has no subcommands — the agent pipes Python via stdin
        # (heredoc) or -c. Intercept only the isolation-relevant flags.
        PASSTHROUGH=()
        while [ $# -gt 0 ]; do
          case "$1" in
            --session|--session=*)
              case "$1" in
                --session) shift; _REQ_SESSION="${{1:-}}"; shift ;;
                *)         _REQ_SESSION="${{1#--session=}}"; shift ;;
              esac
              if [ -n "${{VIBE_TASK_ID:-}}" ]; then
                echo "ERROR: --session is auto-assigned per task ($SESSION)." >&2
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

        # Validate session format: web or web-{{8 hex chars}}
        if [[ ! "$SESSION" =~ ^web(-[0-9a-fA-F]{{8}})?$ ]]; then
            echo "ERROR: session '$SESSION' not allowed. Allowed: web, web-{{8-hex-chars}}" >&2
            exit 1
        fi

        # Auto-start: ensure the web CDP proxy is responding.
        if ! curl -sf -o /dev/null \\
             --max-time 2 "{cdp_http_url}/json/version" 2>/dev/null; then
          _start_resp=$(curl -s -w '\\n%{{http_code}}' \\
            -X POST \\{auth_header}
            -H "Content-Type: application/json" \\
            --max-time 90 \\
            "http://{LOCALHOST}:{port}/api/browser/web/start?force=1" \\
            2>/dev/null) || true
          _start_http=${{_start_resp##*$'\\n'}}
          if ! [ "$_start_http" -ge 200 ] 2>/dev/null \\
             || ! [ "$_start_http" -lt 300 ] 2>/dev/null; then
            echo "ERROR: web browser start API failed (HTTP ${{_start_http:-unavailable}})" >&2
            echo "$_start_resp" | head -5 >&2
            exit 1
          fi
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
        if ! curl -sf -o /dev/null --max-time 2 \\
             "{cdp_http_url}/json/version" 2>/dev/null; then
          echo "ERROR: CDP proxy at {cdp_http_url} not ready after auto-start" >&2
          exit 1
        fi

        # Inject the CDP endpoint + daemon identity as env vars. Each task
        # connects via CDPMuxProxy under its own client id (VIBE_TASK_ID)
        # for tab isolation.
        export BU_NAME="$SESSION"
        CLIENT_ID="${{VIBE_TASK_ID:-$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')}}"
        export BU_CDP_WS="ws://{LOCALHOST}:{proxy_port}/client-${{CLIENT_ID}}"

        # Wedge recovery: bound each call with a hard timeout (perl alarm;
        # macOS has no GNU timeout). On a wedge, reload this session's
        # daemon and surface the error — no blind retry (a 0.13 heredoc
        # may mutate the page, so re-running is unsafe; the agent
        # re-issues against the fresh daemon).
        set +e
        perl -e 'alarm shift; exec @ARGV' 120 "$REAL_BU" ${{PASSTHROUGH[@]+"${{PASSTHROUGH[@]}}"}}
        _vs_rc=$?
        set -e
        if [ "$_vs_rc" -eq 142 ]; then
          echo "[web-wrapper] browser-use timed out (120s) — reloading daemon '$SESSION'" >&2
          BU_NAME="$SESSION" "$REAL_BU" --reload >/dev/null 2>&1 || true
        fi
        exit "$_vs_rc"
    """)

    wrapper_path.write_text(script, encoding='utf-8')
    safe_chmod(wrapper_path, stat.S_IRWXU)  # 700 — owner-only (contains token)
    logger.info(
        'Wrote web browser-use wrapper: %s (proxy=%s)',
        wrapper_path,
        proxy_port,
    )
