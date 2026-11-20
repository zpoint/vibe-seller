"""Should the UI ask a new user to set up AI first?

One system-wide answer, "yes" by default. It turns to "no" when:

- this machine's Claude Code is logged in — checked ONCE, by a
  one-shot job on the app scheduler at server start (``claude auth
  status --json``;
  any way the CLI accepts counts: claude.ai login, an API key/token in
  the env or ``settings.json`` as cc-switch writes it, Bedrock/Vertex);
- or any AI other than the built-in Claude has been added (checked on
  each ask: adding a model ends the prompt at once).

Until that job has answered, the answer is the default ("yes").
Nothing else: no per-user rule, no cache, no re-probing.
"""

import asyncio
from datetime import datetime
import json
import logging
from zoneinfo import ZoneInfo

from apscheduler.triggers.date import DateTrigger

from app.ai.claude_backend_utils import resolve_claude_binary
from app.ai.profiles import DEFAULT_PROFILE_ID, ProfileManager
from app.env_options import Options
from app.utils.timezone import get_server_timezone
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 10.0

_claude_logged_in = False


async def _cli_logged_in() -> bool:
    """``loggedIn`` from ``claude auth status --json`` with the default
    profile's spawn env. It exits 1 when logged out, so the JSON is the
    verdict, not the exit code. Anything unexpected is "no". The output
    names the account: only the flag leaves this function."""
    binary = resolve_claude_binary()
    env = await asyncio.to_thread(
        ProfileManager.get_env_for_profile, DEFAULT_PROFILE_ID
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            'auth',
            'status',
            '--json',
            env=env,
            cwd=str(VIBE_SELLER_DIR),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as e:
        logger.info('AI setup check: cannot run %s (%s)', binary, e)
        return False
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(), timeout=PROBE_TIMEOUT_S
        )
    except TimeoutError:
        logger.info('AI setup check: claude auth status timed out')
        return False
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    try:
        data = json.loads(out.decode('utf-8', errors='replace'))
    except ValueError:
        return False
    return isinstance(data, dict) and data.get('loggedIn') is True


async def run_startup_check() -> None:
    """The one Claude Code check (a scheduler job, run once at start)."""
    global _claude_logged_in
    _claude_logged_in = await _cli_logged_in()
    logger.info('AI setup check: Claude Code logged in = %s', _claude_logged_in)


def register_ai_setup_check(scheduler) -> None:
    """Run :func:`run_startup_check` once, right after the scheduler
    starts — in the background, never blocking server start."""
    scheduler.add_job(
        run_startup_check,
        DateTrigger(run_date=datetime.now(ZoneInfo(get_server_timezone()))),
        id='ai_setup_check',
        replace_existing=True,
        max_instances=1,
        # Registered as "now": run it even if the scheduler gets to it
        # late (busy boot), never drop it as a misfire.
        misfire_grace_time=None,
    )


def _other_ai_configured() -> bool:
    return any(
        p['id'] != DEFAULT_PROFILE_ID for p in ProfileManager.list_profiles()
    )


async def ai_ready() -> bool:
    """False means: show the set-up-AI prompt."""
    if Options.MOCK_CLI.get():
        return True  # test mode: tasks run a mock CLI
    if await asyncio.to_thread(_other_ai_configured):
        return True
    return _claude_logged_in


def reset() -> None:
    """Tests only."""
    global _claude_logged_in
    _claude_logged_in = False
