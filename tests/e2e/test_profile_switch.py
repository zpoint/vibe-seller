"""E2E tests: Profile switching on retry/continue.

Requires: running server with TWO different LLM provider credentials.
Providers are derived from the first two distinct values in
E2E_PROVIDER_MAP (e.g. ``deepseek,deepseek,minimax`` → A=deepseek,
B=minimax).

Guard behaviour:
- Skip if either provider's secrets are missing (locally or CI)

The two profiles must have different base URLs and models.
This ensures a real provider switch, not just a profile ID rename.

Marked with @pytest.mark.e2e so they run only on demand.
"""

import logging
import time

import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import (
    build_profile_env,
    create_task,
    fetch_presets,
    get_distinct_providers,
    get_task,
    poll_task_status,
)

logger = logging.getLogger(__name__)

PROF_A = 'e2e-prof-a'
PROF_B = 'e2e-prof-b'

pytestmark = [pytest.mark.e2e]


# ── Helpers ─────────────────────────────────────────


def _stop_and_wait_failed(client, task_id: str) -> dict:
    """Stop a running task and wait for FAILED status."""
    resp = client.post(f'{BASE_URL}/api/tasks/{task_id}/agent/stop')
    resp.raise_for_status()
    return poll_task_status(client, task_id, {'failed'})


def _resolve_ab() -> tuple[str, str]:
    """Resolve provider A/B names."""
    try:
        name_a, name_b = get_distinct_providers()
    except ValueError:
        pytest.skip('E2E_PROVIDER_MAP missing or <2 distinct providers')
    return name_a, name_b


# ── Fixtures ────────────────────────────────────────


@pytest.fixture(scope='module')
def providers():
    """Resolve and validate both providers."""
    return _resolve_ab()


@pytest.fixture(scope='module')
def test_store(api_client):
    tag = int(time.time())
    resp = api_client.post(
        f'{BASE_URL}/api/stores',
        json={'name': f'e2e-profile-{tag}'},
    )
    resp.raise_for_status()
    return resp.json()


@pytest.fixture(scope='module')
def presets(api_client):
    """Fetch provider presets from server."""
    return fetch_presets(api_client)


@pytest.fixture(scope='module')
def profile_a(api_client, providers, presets):
    """Provider A profile (from server preset)."""
    name_a, _ = providers
    profile = {
        'id': PROF_A,
        'name': 'E2E Provider A',
        'env': build_profile_env(name_a, presets),
    }
    resp = api_client.post(f'{BASE_URL}/api/profiles', json=profile)
    assert resp.status_code in (200, 409)
    return profile


@pytest.fixture(scope='module')
def profile_b(api_client, providers, presets):
    """Provider B profile (from server preset)."""
    _, name_b = providers
    profile = {
        'id': PROF_B,
        'name': 'E2E Provider B',
        'env': build_profile_env(name_b, presets),
    }
    resp = api_client.post(f'{BASE_URL}/api/profiles', json=profile)
    assert resp.status_code in (200, 409)
    return profile


# ── T1: Same profile stop → retry ──────────────────


class TestSameProfileStopRetry:
    def test_same_profile_stop_retry(
        self,
        api_client,
        test_store,
        profile_a,
    ):
        """Create task with profile A -> stop -> retry
        (same profile) -> completes.
        """
        tag = int(time.time())
        task = create_task(
            api_client,
            f'Multi-step task {tag}',
            store_id=test_store['id'],
            profile_id=PROF_A,
            description=(
                f'Use the Write tool to create '
                f'/tmp/profile_retry_{tag}.txt '
                f'with the text "hello". Then use the Read tool '
                f'to read it back and confirm the content.'
            ),
        )
        task_id = task['id']

        data = poll_task_status(
            api_client,
            task_id,
            {'running', 'completed'},
            fail_statuses={'failed'},
        )
        if data['status'] == 'completed':
            pytest.skip('Task completed before stop — too fast')

        # If task is still running, stop it first
        if data['status'] == 'running':
            _stop_and_wait_failed(api_client, task_id)

        # Task is now failed (either agent error or stopped)
        resp = api_client.post(
            f'{BASE_URL}/api/tasks/{task_id}/retry',
            json={'profile_id': PROF_A},
        )
        assert resp.status_code == 200

        final = poll_task_status(
            api_client,
            task_id,
            {'completed'},
            fail_statuses={'failed'},
        )
        assert final['status'] == 'completed'


# ── T2: Profile switch retry (A → B) ─────────────


class TestProfileSwitchRetry:
    def test_profile_switch_retry(
        self,
        api_client,
        test_store,
        profile_a,
        profile_b,
    ):
        """Create with profile A -> stop -> retry with
        profile B -> ai_profile_id changes from A to B.
        """
        tag = int(time.time())
        task = create_task(
            api_client,
            f'Multi-step task {tag}',
            store_id=test_store['id'],
            profile_id=PROF_A,
            description=(
                f'Use the Write tool to create '
                f'/tmp/switch_retry_{tag}.txt '
                f'with the text "hello". Then use the Read tool '
                f'to read it back and confirm the content.'
            ),
        )
        task_id = task['id']

        data = poll_task_status(
            api_client,
            task_id,
            {'running', 'completed'},
            fail_statuses={'failed'},
        )
        if data['status'] == 'completed':
            pytest.skip('Task completed before stop — too fast')

        # Assert: profile is A before the switch
        before = get_task(api_client, task_id)
        assert before.get('ai_profile_id') == PROF_A

        # If task is still running, stop it first
        if data['status'] == 'running':
            _stop_and_wait_failed(api_client, task_id)

        # Task is now failed (either agent error or stopped)
        # Switch to profile B (different provider)
        resp = api_client.post(
            f'{BASE_URL}/api/tasks/{task_id}/retry',
            json={'profile_id': PROF_B},
        )
        assert resp.status_code == 200
        assert resp.json()['profile_id'] == PROF_B

        final = poll_task_status(
            api_client,
            task_id,
            {'completed'},
            fail_statuses={'failed'},
        )
        assert final['status'] == 'completed'

        # Assert: profile changed to B
        assert final.get('ai_profile_id') == PROF_B
        assert final.get('ai_profile_id') != PROF_A
