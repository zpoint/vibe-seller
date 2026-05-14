"""E2E tests: Stop → Retry flows.

Split from test_conversation_lifecycle.py for parallel execution.

Requires: running server with real LLM credentials.
Marked with @pytest.mark.e2e so they run only on demand.
"""

import logging
import time

import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import (
    create_task,
    get_task,
    poll_task_status,
)

logger = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.WARNING)

pytestmark = [pytest.mark.e2e]


# ── Fixtures ────────────────────────────────────────


@pytest.fixture(scope='module')
def test_store(api_client):
    """Create a store for stop/retry tests."""
    tag = int(time.time())
    resp = api_client.post(
        f'{BASE_URL}/api/stores',
        json={'name': f'e2e-stop-retry-{tag}'},
    )
    resp.raise_for_status()
    return resp.json()


# ── E2: Stop → Retry → Complete ───────────────────


class TestE2StopRetry:
    def test_stop_then_retry(self, api_client, test_store):
        """E2: Create → execute → stop → retry → complete."""
        tag = int(time.time())
        task = create_task(
            api_client,
            f'Use the Write tool to create retry-{tag}.txt '
            f'with the text "done"',
            plan_mode=False,
            store_id=test_store['id'],
        )
        task_id = task['id']

        # Wait for running or completed
        data = poll_task_status(
            api_client,
            task_id,
            {'running', 'completed'},
            fail_statuses={'failed'},
        )

        if data['status'] != 'running':
            logger.info('Task completed before stop; skipping')
            return

        # Stop
        resp = api_client.post(
            f'{BASE_URL}/api/tasks/{task_id}/agent/stop',
        )
        assert resp.status_code == 200

        data2 = poll_task_status(api_client, task_id, {'failed'})
        assert data2['status'] == 'failed'
        # The failed run should have reached Claude Code's init
        # event before stop, so session_id is expected to be set
        # even though the overall run failed.
        sid_before_retry = data2.get('session_id')
        logger.info('Stopped successfully (session_id=%s)', sid_before_retry)

        # Retry
        resp = api_client.post(
            f'{BASE_URL}/api/tasks/{task_id}/retry',
        )
        assert resp.status_code == 200

        # Path D (retry) contract: session_id must be cleared
        # immediately on /retry. If it wasn't, the next run would
        # --resume a transcript from the aborted attempt and pick
        # up that run's state.
        after_retry_post = get_task(api_client, task_id)
        assert after_retry_post.get('session_id') is None, (
            'retry must clear session_id synchronously (task was '
            f'session_id={sid_before_retry!r} before retry)'
        )

        # Wait for completion
        final = poll_task_status(
            api_client,
            task_id,
            {'completed'},
            fail_statuses={'failed'},
        )
        assert final['status'] == 'completed'
        # A fresh session_id is populated by the retry run. When
        # we had one before retry, the new one must be different —
        # same id would mean the retry didn't actually spawn a
        # fresh session.
        sid_after_retry = final.get('session_id')
        assert sid_after_retry, 'Retry run must populate a fresh session_id'
        if sid_before_retry:
            assert sid_after_retry != sid_before_retry, (
                'Retry reused the aborted run session_id; it '
                'should have been discarded.'
            )
        logger.info(
            'E2: Stop-retry completed (session_id %s → %s)',
            sid_before_retry,
            sid_after_retry,
        )
