"""Store-less orchestrator "web" browser-use wrapper generation.

Generates ``$VIBE_HOME/bin/_web/browser-use`` — the wrapper for no-store
(orchestrator) tasks. Split out of ``wrapper.py`` (per-store wrapper) so
each stays a focused, readable module; keep the shared self-heal +
URL-shape guard logic in sync between the two.
"""

import logging
from pathlib import Path
import shutil
import stat
import sys
import textwrap

from app.config import (
    BACKEND_PORT,
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
    """Generate the orchestrator "web" browser-use wrapper.

    Creates ``~/.vibe-seller/bin/_web/browser-use`` for no-store
    (orchestrator) tasks. Unlike the per-store wrapper this browser is
    NOT tied to any store: it's a single generic Chrome session for
    neutral public web work (search, tracking/logistics pages,
    research). It is Chrome-only — there is no Ziniao routing and no
    ``{slug}-aux`` split — so this is a deliberately simpler sibling of
    :func:`write_browser_use_wrapper`.

    Session naming: ``web`` (interactive) or ``web-{VIBE_TASK_ID[:8]}``
    (per task, so concurrent orchestrator tasks get isolated daemons,
    multiplexed through CDPMuxProxy by ``VIBE_TASK_ID``). Auto-start
    hits ``POST /api/browser/web/start`` (no store id).

    NOTE: the self-heal + URL-shape guard here mirror
    :func:`write_browser_use_wrapper`; keep the two in sync. They are
    kept as separate generators because the store wrapper's Ziniao/aux
    routing does not apply to the store-less web browser, and coupling
    them would put that battle-tested store path at risk.
    """
    wrapper_dir = _BIN_DIR / WEB_BROWSER_SLUG
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = wrapper_dir / 'browser-use'

    # Same binary-resolution rationale as write_browser_use_wrapper:
    # prefer the browser-use next to the daemon interpreter, never
    # .resolve() (uv venvs symlink to a base Python with its own,
    # possibly older, browser-use).
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

    headed_flag = '--headed ' if not headless else ''

    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Auto-generated browser-use wrapper for the orchestrator web
        # browser (store-less). Do not edit — regenerated on task start.
        set -euo pipefail

        # Strip system proxy env so the daemon's CDP WebSocket goes
        # straight to localhost instead of via ClashX/etc.
        unset http_proxy HTTP_PROXY https_proxy HTTPS_PROXY all_proxy ALL_PROXY

        REAL_BU="{real_bu}"

        # Per-task session: each task gets its own daemon so concurrent
        # orchestrator tasks never collide on one session config.
        if [ -n "${{VIBE_TASK_ID:-}}" ]; then
          SESSION="web-${{VIBE_TASK_ID:0:8}}"
        else
          SESSION="web"
        fi

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

        # Validate session format: web or web-{{8 hex chars}}
        if [[ ! "$SESSION" =~ ^web(-[0-9a-fA-F]{{8}})?$ ]]; then
            echo "ERROR: session '$SESSION' not allowed. Allowed: web, web-{{8-hex-chars}}" >&2
            exit 1
        fi

        # URL-shape validation for `open` (mirrors the store wrapper):
        # a shell that ate '?'/'&' before we ran leaves a mangled URL;
        # fail loudly on the first call instead of silently no-op'ing.
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
            http://*|https://*) : ;;
            about:*|file://*)   : ;;
            *)
              echo "ERROR: 'browser-use $_bu_subcmd' expects an http(s)://, about:, or file:// URL." >&2
              echo "       Got: ${{_bu_url:-<missing>}}" >&2
              echo "" >&2
              echo "Likely cause: the calling shell parsed special characters" >&2
              echo "in your URL. URLs with '?', '&', or '#' MUST be quoted:" >&2
              echo "  browser-use open 'https://example.com/page?a=1&b=2'" >&2
              exit 2
              ;;
          esac
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

        # Inject --cdp-url so each task connects via CDPMuxProxy under
        # its own client id (VIBE_TASK_ID) for tab isolation.
        CDP_ARGS=()
        CLIENT_ID="${{VIBE_TASK_ID:-$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')}}"
        CDP_ARGS+=("--cdp-url" "ws://{LOCALHOST}:{proxy_port}/client-${{CLIENT_ID}}")

        # Self-heal wedged daemons (mirrors the store wrapper; all web
        # sessions go through the proxy so the policy applies to all).
        _vs_cmd=""
        for _vs_tok in ${{PASSTHROUGH[@]+"${{PASSTHROUGH[@]}}"}}; do
          case "$_vs_tok" in -*) ;; *) _vs_cmd="$_vs_tok"; break ;; esac
        done
        _vs_policy="none"
        case "$_vs_cmd" in
          open|navigate) _vs_policy="nav" ;;
          state|get)     _vs_policy="read" ;;
          eval)          _vs_policy="eval" ;;
        esac

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
            _vs_connfail=0
            printf '%s' "$_vs_out" | grep -qiE \\
              'BrowserStartEvent.*timed out|connect\\(\\) timed out|CDP connection to ws.*(too slow|unresponsive)|Client is stopping|Browser.*not.*(start|connect)' \\
              && _vs_connfail=1
            _vs_alarm=0; [ "$_vs_rc" -eq 142 ] && _vs_alarm=1
            if [ "$_vs_connfail" = "1" ] || [ "$_vs_alarm" = "1" ]; then
              echo "[web-wrapper] '$_vs_cmd' wedged (rc=$_vs_rc) — recovering daemon" >&2
              pkill -9 -f "browser_use.skill_cli.daemon.*$SESSION" 2>/dev/null || true
              if [ "$_vs_policy" = "nav" ]; then
                curl -sf -o /dev/null --max-time 15 \\
                  "{cdp_http_url}/vibe/reset-tabs?client=${{CLIENT_ID:-}}" 2>/dev/null || true
              fi
              _vs_retry=0
              case "$_vs_policy" in
                nav|read) _vs_retry=1 ;;
                eval)     [ "$_vs_connfail" = "1" ] && _vs_retry=1 ;;
              esac
              if [ "$_vs_retry" = "1" ]; then
                echo "[web-wrapper] retrying '$_vs_cmd' once" >&2
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

        exec "$REAL_BU" {headed_flag}--session "$SESSION" \\
          ${{CDP_ARGS[@]+"${{CDP_ARGS[@]}}"}} "${{PASSTHROUGH[@]}}"
    """)

    wrapper_path.write_text(script, encoding='utf-8')
    safe_chmod(wrapper_path, stat.S_IRWXU)  # 700 — owner-only (contains token)
    logger.info(
        'Wrote web browser-use wrapper: %s (proxy=%s)',
        wrapper_path,
        proxy_port,
    )
