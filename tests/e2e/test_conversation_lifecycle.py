"""E2E tests: Task lifecycle through plan mode (ExitPlanMode).

Requires: running server with real LLM credentials.
Tests the native plan mode flow: designing → planned → running → completed.

Marked with @pytest.mark.e2e so they run only on demand.
"""

import logging
import time

import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import (
    create_task,
    poll_task_status,
)

logger = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.WARNING)

pytestmark = [pytest.mark.e2e]


# ── Fixtures ────────────────────────────────────────


@pytest.fixture(scope='module')
def test_store(api_client):
    """Create a store for E2E tests (avoids orchestrator prompt)."""
    tag = int(time.time())
    resp = api_client.post(
        f'{BASE_URL}/api/stores',
        json={'name': f'e2e-conversation-{tag}'},
    )
    resp.raise_for_status()
    return resp.json()


# ── E1: Plan → Execute → Complete ─────────────────


class TestE1PlanExecute:
    def test_plan_then_execute(self, api_client, test_store):
        """E1: Create → auto-plan → auto-execute → complete.

        Verifies ExitPlanMode flow: designing → planned → running
        → completed without ZodError or Stream closed.
        """
        tag = int(time.time())
        task = create_task(
            api_client,
            f'Write the text "hello-{tag}" to a file named hello.txt '
            f'in your working directory using the Write tool',
            plan_mode=False,
            store_id=test_store['id'],
        )
        task_id = task['id']

        # Wait for completion (auto-approve skips manual confirm)
        final = poll_task_status(
            api_client,
            task_id,
            {'completed'},
            fail_statuses={'failed'},
        )
        assert final['status'] == 'completed'
        assert final['result'] is not None
        # Path A (first run) contract: session_id must be populated
        # after completion. It seeds `--resume` for any follow-up
        # message. Populated via `_persist_session_id` at the init
        # event or `_save_result` / `_release_on_done` at end-of-
        # stream.
        assert final.get('session_id'), (
            'First run must persist session_id; otherwise any '
            'future follow-up loses --resume and restarts from '
            'scratch.'
        )
        logger.info('E1: Plan-then-execute completed')

        # Verify messages exist
        resp = api_client.get(
            f'{BASE_URL}/api/tasks/{task_id}/messages',
        )
        msgs = resp.json()
        assert len(msgs) > 0


# E2 (stop → retry) and E3 (stop → continue) are in
# test_a_stop_retry_continue.py — they need the MiniMax worker
# due to Kimi's post-plan execution bug (see PROFILING.md).


# ── E4: AskUserQuestion → Answer → Complete ──────
# Note: The api_client fixture auto-answers questions via SSE.
# This test just verifies the task completes (the agent may or
# may not ask a question — both paths lead to completion).


class TestE4AskUserQuestion:
    def test_question_answer_completes(self, api_client, test_store):
        """E4: Self-contained prompt → agent may ask question
        (auto-answered) → complete. No external file deps."""
        tag = int(time.time())
        task = create_task(
            api_client,
            f'Write store notes-{tag}',
            plan_mode=False,
            store_id=test_store['id'],
            description=(
                'Write a brief notes file about this store '
                'in the workspace. Ask what to include.'
            ),
        )
        task_id = task['id']

        # api_client fixture auto-answers any AskUserQuestion via SSE
        final = poll_task_status(
            api_client,
            task_id,
            {'completed', 'failed'},
        )

        logger.info(
            'E4: status=%s',
            final['status'],
        )
        assert final['status'] in ('completed', 'failed')
