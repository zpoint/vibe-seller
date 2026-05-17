"""
E2E test: skill prerequisite hook enforces load order.

The bug this protects against: when an agent calls
``Skill(noon-ads)`` without first loading ``noon-shared``, the
backend hook must deny the call with a clear retry message — and
after the agent loads ``noon-shared``, the same ``Skill(noon-ads)``
call must succeed and the prerequisite's content reaches the agent.

This mirrors a real failure mode observed in production: an agent
session skipped the prerequisite, missed the login URL in
``noon-shared/SKILL.md``, and invented a non-existent noon URL
instead. The hook turns that prose contract into a mechanism.

Verification strategy: we cannot trust the agent's free-text to
prove the hook fired (the agent could narrate it without it
actually happening). Instead we read the **server hook log** —
the deny path emits ``Skill prereq: agent <task_id_prefix>
tried 'X' without ...`` via ``logger.info``, which is the
authoritative record. We assert the marker exists for the
denied skill load, and assert the successful tool_result
sequence exists in ``task_messages``.

Requires: real ``claude`` CLI + LLM API key + running server.
"""

import logging
from pathlib import Path
import re
import time

import httpx
import pytest

from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    create_store,
    create_task,
    get_messages,
    login,
    poll_task_status,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e]

# Unique substring that only appears in noon-shared/SKILL.md among
# the loaded skills — used to assert the prerequisite's content
# actually reached the agent after the corrected load order.
NOON_SHARED_MARKER = 'https://login.noon.partners/en/'

# Server hook log lines look like:
#   ... app.ai.claude_backend_hooks INFO Skill prereq: agent abc12345 \
#       tried 'noon-ads' without "Skill 'noon-ads' requires 'noon-shared'..."
#
# Candidates the e2e test searches for the server log, in order:
# - docker-runner convention (docker-entrypoint.sh symlinks here)
# - public-repo start.sh (writes to ``$LOG_DIR/backend.log`` where
#   LOG_DIR defaults to ``logs`` or ``~/.vibe-seller/logs``)
# - dev-repo start.sh (port-suffixed)
# The first one that exists and is non-empty wins.
HOOK_LOG_CANDIDATES = (
    Path('logs/server_stdout.log'),
    Path('logs/backend_7777.log'),
    Path('logs/backend.log'),
    Path.home() / '.vibe-seller' / 'logs' / 'backend_7777.log',
    Path.home() / '.vibe-seller' / 'logs' / 'backend.log',
)


@pytest.fixture(scope='module')
def api_client():
    client = httpx.Client(timeout=30)
    login(client)
    yield client
    client.close()


@pytest.fixture(scope='module')
def prereq_store(api_client: httpx.Client) -> dict:
    ts = int(time.time())
    return create_store(
        api_client,
        f'prereq-hook-test-{ts}',
        browser_backend='chrome',
    )


def _read_hook_log() -> str:
    """Read the server log file from whichever path the deploy
    happens to use. Returns the first non-empty match.
    """
    for path in HOOK_LOG_CANDIDATES:
        if path.is_file() and path.stat().st_size > 0:
            return path.read_text(encoding='utf-8', errors='replace')
    return ''


def _hook_denied(log_text: str, task_id_prefix: str, skill: str) -> bool:
    """True iff the backend hook emitted a Skill-prereq deny for
    this task's attempt to load `skill`.

    The deny path in ``_handle_hook_callback`` logs:
        Skill prereq: agent <prefix> tried '<skill>' without ...
    """
    pat = re.compile(
        rf'Skill prereq: agent {re.escape(task_id_prefix)}'
        rf"\s+tried\s+'{re.escape(skill)}'"
    )
    return bool(pat.search(log_text))


class TestSkillPrereqHookEndToEnd:
    """Full agent-loop verification of the Skill prereq hook."""

    def test_direct_load_denied_then_succeeds_after_prereq(
        self,
        api_client: httpx.Client,
        prereq_store: dict,
    ):
        """The agent must observe both halves of the contract.

        1. ``Skill(noon-ads)`` without ``noon-shared`` → backend
           hook logs a deny line for this task.
        2. The agent then loads ``noon-shared`` and retries; both
           Skill tool_results appear in task_messages and the
           noon-shared body (containing the noon login URL)
           reaches the agent's context.

        We grep the server log (authoritative source) for the deny
        event rather than trusting the agent's transcript, because
        the agent could narrate a deny that never happened.
        """
        task = create_task(
            api_client,
            title='Skill prereq hook smoke test',
            store_id=prereq_store['id'],
            description=(
                'You are validating a backend safety hook — '
                'do NOT open a browser, do NOT do real work.\n\n'
                '1. First, call the Skill tool with '
                '`skill="noon-ads"` directly. The backend WILL '
                'reject it because `noon-ads` requires '
                '`noon-shared`. That rejection is expected — '
                'continue.\n\n'
                '2. Then call the Skill tool with '
                '`skill="noon-shared"`. It will succeed.\n\n'
                '3. Then call the Skill tool with '
                '`skill="noon-ads"` again. It will now succeed.\n\n'
                '4. Echo back the noon login URL '
                '(https://login.noon.partners/en/) on its own line '
                'in your final response. Copy it exactly from the '
                'noon-shared content you loaded.'
            ),
        )
        task_id = task['id']
        task_id_prefix = task_id[:8]
        logger.info('Created prereq-hook task %s', task_id_prefix)

        result = poll_task_status(
            api_client,
            task_id,
            target_statuses={'completed'},
            fail_statuses={'failed'},
            timeout=PIPELINE_TIMEOUT,
        )
        assert result['status'] == 'completed', (
            f'Task failed: {result.get("error", "unknown")}'
        )

        # ── Assertion 1: the hook actually fired a deny ─────────
        # Read the server log AFTER task completion so we capture
        # every event for this task_id.
        log_text = _read_hook_log()
        assert log_text, (
            f'No server log found at any of {[str(p) for p in HOOK_LOG_CANDIDATES]}'
            " — can't verify hook fired."
        )
        assert _hook_denied(log_text, task_id_prefix, 'noon-ads'), (
            f"Hook log has no 'Skill prereq: agent {task_id_prefix} "
            "tried 'noon-ads' without ...' entry. The backend never "
            'denied the bad load order, which is the whole point '
            'of this test.\n\n'
            'Log excerpt around task_id:\n'
            + '\n'.join(
                line for line in log_text.splitlines() if task_id_prefix in line
            )[:2000]
        )

        # ── Assertion 2: noon-shared content reached the agent ──
        # The login URL only lives in noon-shared/SKILL.md, so its
        # presence in task_messages proves the prereq successfully
        # loaded after the retry — which can only happen if the
        # agent corrected its load order in response to the deny.
        messages = get_messages(api_client, task_id)
        all_text = (
            ' '.join(m.get('content', '') for m in messages)
            + ' '
            + (result.get('result') or '')
        )
        assert NOON_SHARED_MARKER in all_text, (
            f'Expected the noon login URL {NOON_SHARED_MARKER!r} '
            "(from noon-shared) to appear in the agent's "
            'transcript after the prerequisite load succeeded. '
            'Either the agent never loaded noon-shared, or its '
            'content never reached the context.\n'
            f'Transcript first 800 chars: {all_text[:800]!r}'
        )
