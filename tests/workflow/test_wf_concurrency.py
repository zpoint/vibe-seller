"""Workflow tests for agent concurrency settings and semaphore behaviour.

Covers:
- max_agent_concurrency settings CRUD (GET/PUT /api/settings)
- Tasks stay PENDING until concurrency slot is acquired
- Live semaphore update when settings change
"""

import asyncio

import pytest

from app.models.app_settings import AppSettings
from app.task_states import TaskStatus
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


class TestConcurrencySettings:
    """GET / PUT /api/settings for max_agent_concurrency."""

    async def test_get_returns_default(self, admin_client):
        """GET /api/settings returns default concurrency from env."""
        r = await admin_client.get('/api/settings')
        assert r.status_code == 200
        data = r.json()
        assert 'max_agent_concurrency' in data
        # Default is '2' from env_options
        assert int(data['max_agent_concurrency']) >= 1

    async def test_put_persists_value(
        self, admin_client, override_async_session
    ):
        """PUT /api/settings persists concurrency to DB."""
        r = await admin_client.put(
            '/api/settings',
            json={'max_agent_concurrency': 5},
        )
        assert r.status_code == 200
        assert r.json()['ok'] is True

        # Verify persisted in DB
        async with override_async_session() as db:
            row = await db.get(AppSettings, 'max_agent_concurrency')
            assert row is not None
            assert row.value == '5'

        # Verify GET returns the new value
        r = await admin_client.get('/api/settings')
        assert r.json()['max_agent_concurrency'] == '5'

    async def test_put_clamps_minimum_to_1(
        self, admin_client, override_async_session
    ):
        """Values < 1 are clamped to 1."""
        await admin_client.put(
            '/api/settings',
            json={'max_agent_concurrency': 0},
        )
        async with override_async_session() as db:
            row = await db.get(AppSettings, 'max_agent_concurrency')
            assert row is not None
            assert row.value == '1'

    async def test_put_ignores_invalid(
        self, admin_client, override_async_session
    ):
        """Non-integer values are ignored (no DB row created)."""
        await admin_client.put(
            '/api/settings',
            json={'max_agent_concurrency': 'abc'},
        )
        async with override_async_session() as db:
            row = await db.get(AppSettings, 'max_agent_concurrency')
            # No row created for invalid value
            assert row is None

    async def test_put_requires_admin(self, member_client):
        """Non-admin users get 403."""
        r = await member_client.put(
            '/api/settings',
            json={'max_agent_concurrency': 3},
        )
        assert r.status_code == 403


class TestConcurrencySemaphore:
    """Tasks stay PENDING until concurrency slot is acquired."""

    async def test_second_task_stays_pending_when_slot_full(
        self, admin_client, install_fake_agent
    ):
        """With concurrency=1, second task stays PENDING while
        first is active."""
        # Gate-held scenario: holds the agent slot until released
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(gate=gate)

        # Set concurrency to 1 via settings
        await admin_client.put(
            '/api/settings',
            json={'max_agent_concurrency': 1},
        )

        r = await admin_client.post('/api/stores', json={'name': 'Sem Store'})
        store_id = r.json()['id']

        # Create first task — should acquire the slot
        r1 = await admin_client.post(
            '/api/tasks',
            json={'title': 'First', 'store_id': store_id},
        )
        t1_id = r1.json()['id']
        await install_fake_agent.wait_started(t1_id)

        # First task should be active
        r = await admin_client.get(f'/api/tasks/{t1_id}')
        t1_status = r.json()['status']
        assert t1_status in (
            TaskStatus.DESIGNING,
            TaskStatus.RUNNING,
        ), f'Expected first task active, got {t1_status}'

        # Create second task — should stay pending (slot full)
        r2 = await admin_client.post(
            '/api/tasks',
            json={'title': 'Second', 'store_id': store_id},
        )
        t2_id = r2.json()['id']

        # Give queue scheduler a tick to attempt dispatch
        await asyncio.sleep(0.1)

        r = await admin_client.get(f'/api/tasks/{t2_id}')
        t2_status = r.json()['status']
        assert t2_status in (
            TaskStatus.PENDING,
            TaskStatus.QUEUED,
        ), f'Expected second task pending, got {t2_status}'

        # Release first task, wait for both to complete
        gate.set()
        await wait_for_task(admin_client, t1_id, timeout=15)
        await wait_for_task(admin_client, t2_id, timeout=15)

    async def test_both_tasks_active_when_slots_available(
        self, admin_client, install_fake_agent
    ):
        """With concurrency=2, both tasks can be active."""
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(gate=gate)

        await admin_client.put(
            '/api/settings',
            json={'max_agent_concurrency': 2},
        )

        r = await admin_client.post('/api/stores', json={'name': 'Dual Store'})
        store_id = r.json()['id']

        t_ids = []
        for i in range(2):
            r = await admin_client.post(
                '/api/tasks',
                json={
                    'title': f'Dual {i}',
                    'store_id': store_id,
                },
            )
            t_ids.append(r.json()['id'])
            await install_fake_agent.wait_started(r.json()['id'])

        active = 0
        for tid in t_ids:
            r = await admin_client.get(f'/api/tasks/{tid}')
            if r.json()['status'] in (
                TaskStatus.DESIGNING,
                TaskStatus.RUNNING,
            ):
                active += 1
        assert active == 2, f'Expected 2 active tasks, got {active}'

        gate.set()
        for tid in t_ids:
            await wait_for_task(admin_client, tid, timeout=15)
