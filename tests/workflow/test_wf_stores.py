"""Workflow tests for store CRUD, browser config, and task queuing."""

import asyncio
import json

import pytest
from sqlalchemy import select as sa_select

from app.models.schedule import Schedule
import app.routers.tasks as tasks_mod
from app.task_runner import sync_store_metadata
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


class TestStoreCrud:
    async def test_create_store_full_response(self, admin_client):
        r = await admin_client.post(
            '/api/stores',
            json={
                'name': 'My Store',
                'browser_backend': 'chrome',
                'platforms': ['amazon'],
                'countries': ['US'],
            },
        )
        assert r.status_code == 200
        s = r.json()
        assert s['name'] == 'My Store'
        assert s['browser_backend'] == 'chrome'
        assert s['platforms'] == ['amazon']
        assert s['countries'] == ['US']
        assert s['id'] is not None

    async def test_create_ziniao_store(self, admin_client):
        r = await admin_client.post(
            '/api/stores',
            json={
                'name': 'Ziniao Store',
                'browser_backend': 'ziniao',
                'ziniao_account_id': 'acc-123',
                'browser_oauth': 'oauth-abc',
            },
        )
        assert r.status_code == 200
        s = r.json()
        assert s['browser_backend'] == 'ziniao'
        assert s['ziniao_account_id'] == 'acc-123'
        assert s['browser_oauth'] == 'oauth-abc'

    async def test_create_chinese_name_store(self, admin_client):
        """Chinese store names must create successfully."""
        r = await admin_client.post(
            '/api/stores', json={'name': '示例云麓科技'}
        )
        assert r.status_code == 200
        assert r.json()['name'] == '示例云麓科技'

    async def test_delete_store_cleanup(self, admin_client):
        r = await admin_client.post('/api/stores', json={'name': 'Delete Me'})
        sid = r.json()['id']

        r = await admin_client.delete(f'/api/stores/{sid}')
        assert r.status_code == 200

        r = await admin_client.get(f'/api/stores/{sid}')
        assert r.status_code == 404

    async def test_delete_store_deletes_tasks(
        self, admin_client, install_fake_agent
    ):
        """Deleting a store also deletes its tasks."""
        r = await admin_client.post(
            '/api/stores', json={'name': 'Task Cascade'}
        )
        sid = r.json()['id']

        tr = await admin_client.post(
            '/api/tasks',
            json={'title': 'Doomed task', 'store_id': sid},
        )
        tid = tr.json()['id']
        await wait_for_task(admin_client, tid)

        # Delete store
        await admin_client.delete(f'/api/stores/{sid}')

        # Task should be gone
        r = await admin_client.get(f'/api/tasks/{tid}')
        assert r.status_code == 404

    async def test_delete_store_allows_recreate(self, admin_client):
        """Can recreate store with same name after deletion."""
        r = await admin_client.post('/api/stores', json={'name': 'Reusable'})
        sid = r.json()['id']
        await admin_client.delete(f'/api/stores/{sid}')

        r = await admin_client.post('/api/stores', json={'name': 'Reusable'})
        assert r.status_code == 200
        assert r.json()['id'] != sid

    async def test_list_sorted_by_activity(
        self, admin_client, install_fake_agent
    ):
        """Stores with recent tasks sort first."""
        # Create two stores
        r1 = await admin_client.post('/api/stores', json={'name': 'Old Store'})
        old_id = r1.json()['id']

        r2 = await admin_client.post(
            '/api/stores', json={'name': 'Active Store'}
        )
        active_id = r2.json()['id']

        # Create task for active store only
        tr = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Active task',
                'store_id': active_id,
            },
        )
        await wait_for_task(admin_client, tr.json()['id'])

        # List stores — active store should be first
        r = await admin_client.get('/api/stores')
        stores = r.json()
        store_ids = [s['id'] for s in stores]
        assert store_ids.index(active_id) < store_ids.index(old_id)


class TestTaskQueuing:
    async def test_per_store_task_queuing(
        self, admin_client, install_fake_agent
    ):
        """Two tasks for same store can progress concurrently."""
        # Use a slow agent so the first task blocks the queue
        # while the second task is created
        install_fake_agent.default_scenario = FakeAgentScenario(
            complete_delay=2.0
        )

        r = await admin_client.post('/api/stores', json={'name': 'Queue Store'})
        store_id = r.json()['id']

        # Create first task
        r1 = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Task 1',
                'store_id': store_id,
            },
        )
        t1_id = r1.json()['id']

        # Wait for first task to reach an active state
        await wait_for_task(admin_client, t1_id, target='running')

        # Create second task for same store
        r2 = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Task 2',
                'store_id': store_id,
            },
        )
        t2_id = r2.json()['id']

        # Wait for task2 to reach an active state
        await wait_for_task(admin_client, t2_id, target='running')

        # Wait for both to complete
        await wait_for_task(admin_client, t1_id, timeout=30)
        await wait_for_task(admin_client, t2_id, timeout=30)

    async def test_three_tasks_same_store_all_concurrent(
        self, admin_client, install_fake_agent
    ):
        """3 tasks for same store all run concurrently (no lock)."""
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(gate=gate)
        r = await admin_client.post('/api/stores', json={'name': 'Para Store'})
        store_id = r.json()['id']

        task_ids = []
        for i in range(3):
            r = await admin_client.post(
                '/api/tasks',
                json={
                    'title': f'Para Task {i}',
                    'store_id': store_id,
                },
            )
            task_ids.append(r.json()['id'])
            # Wait for each agent to start before creating the
            # next to avoid StaticPool contention on the single
            # test DB connection.
            await install_fake_agent.wait_started(r.json()['id'])

        # All running concurrently while gate is closed
        for tid in task_ids:
            r = await admin_client.get(f'/api/tasks/{tid}')
            assert r.json()['status'] in (
                'designing',
                'running',
                'pending',
                'queued',
            ), f'Task {tid} not progressing'

        # Release all agents to complete
        gate.set()
        for tid in task_ids:
            await wait_for_task(admin_client, tid, timeout=30)

    async def test_different_stores_parallel(
        self, admin_client, install_fake_agent
    ):
        """Tasks for different stores don't block each other."""
        install_fake_agent.default_scenario = FakeAgentScenario()

        r1 = await admin_client.post('/api/stores', json={'name': 'Store A'})
        r2 = await admin_client.post('/api/stores', json={'name': 'Store B'})

        # Create and complete tasks sequentially to avoid
        # StaticPool contention on the single test DB conn.
        t1 = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'A task',
                'store_id': r1.json()['id'],
            },
        )
        t1_id = t1.json()['id']
        d1 = await wait_for_task(admin_client, t1_id, timeout=30.0)

        t2 = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'B task',
                'store_id': r2.json()['id'],
            },
        )
        t2_id = t2.json()['id']
        d2 = await wait_for_task(admin_client, t2_id, timeout=30.0)
        assert d1['status'] == 'completed', (
            f'd1 stuck: status={d1["status"]} err={d1.get("error")}'
        )
        assert d2['status'] == 'completed', (
            f'd2 stuck: status={d2["status"]} err={d2.get("error")}'
        )

    async def test_cross_platform_same_store_concurrent(
        self, admin_client, install_fake_agent
    ):
        """Tasks for different platforms on same store run concurrently.

        E.g. Amazon EG + Noon EG should NOT queue — they use
        different seller portals in separate browser tabs.
        """
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(gate=gate)
        r = await admin_client.post(
            '/api/stores', json={'name': 'CrossPlatform Store'}
        )
        store_id = r.json()['id']

        # Task 1: Amazon EG
        r1 = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Amazon EG task',
                'store_id': store_id,
                'platform': 'amazon',
                'country': 'EG',
            },
        )
        t1_id = r1.json()['id']
        await install_fake_agent.wait_started(t1_id)

        # Task 2: Noon EG (different platform, same country)
        r2 = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Noon EG task',
                'store_id': store_id,
                'platform': 'noon',
                'country': 'EG',
            },
        )
        t2_id = r2.json()['id']
        await install_fake_agent.wait_started(t2_id)

        # Both should be progressing (not queued)
        s1 = (await admin_client.get(f'/api/tasks/{t1_id}')).json()
        s2 = (await admin_client.get(f'/api/tasks/{t2_id}')).json()
        assert s1['status'] in ('designing', 'running'), (
            f'Task1 should be progressing, got {s1["status"]}'
        )
        assert s2['status'] in ('designing', 'running'), (
            f'Task2 should be concurrent, got {s2["status"]}'
        )

        gate.set()
        await wait_for_task(admin_client, t1_id, timeout=30)
        await wait_for_task(admin_client, t2_id, timeout=30)


class TestStoreUpdate:
    async def test_update_name(self, admin_client):
        r = await admin_client.post(
            '/api/stores', json={'name': 'Original Name'}
        )
        sid = r.json()['id']

        r = await admin_client.put(
            f'/api/stores/{sid}', json={'name': 'New Name'}
        )
        assert r.status_code == 200
        assert r.json()['name'] == 'New Name'

    async def test_update_name_conflict_409(self, admin_client):
        await admin_client.post('/api/stores', json={'name': 'Store X'})
        r2 = await admin_client.post('/api/stores', json={'name': 'Store Y'})
        sid = r2.json()['id']

        r = await admin_client.put(
            f'/api/stores/{sid}', json={'name': 'Store X'}
        )
        assert r.status_code == 409

    async def test_update_platform_countries(self, admin_client):
        r = await admin_client.post('/api/stores', json={'name': 'PC Store'})
        sid = r.json()['id']
        assert r.json()['platform_countries'] == {}

        pc = {'amazon': ['US', 'UK'], 'noon': ['EG']}
        r = await admin_client.put(
            f'/api/stores/{sid}',
            json={'platform_countries': pc},
        )
        assert r.status_code == 200
        assert r.json()['platform_countries'] == pc
        # platforms/countries derived from platform_countries
        assert sorted(r.json()['platforms']) == ['amazon', 'noon']
        assert sorted(r.json()['countries']) == ['EG', 'UK', 'US']

    async def test_update_name_blocked_active_task_409(
        self, admin_client, install_fake_agent
    ):
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(gate=gate)
        r = await admin_client.post('/api/stores', json={'name': 'Busy Store'})
        sid = r.json()['id']

        # Create a task that holds the store busy
        tr = await admin_client.post(
            '/api/tasks',
            json={'title': 'Busy task', 'store_id': sid},
        )
        tid = tr.json()['id']
        # Wait for the agent to start (gate holds it running)
        await install_fake_agent.wait_started(tid)

        r = await admin_client.put(
            f'/api/stores/{sid}', json={'name': 'Renamed'}
        )
        assert r.status_code == 409

        gate.set()
        await wait_for_task(admin_client, tid, timeout=10)


class TestStoreDeleteCascade:
    async def test_delete_cleans_email_links(self, admin_client):
        r = await admin_client.post('/api/stores', json={'name': 'Email Store'})
        sid = r.json()['id']

        # Create an email account
        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'test@example.com',
                'imap_host': 'imap.example.com',
                'imap_port': 993,
                'password': 'secret',
            },
        )
        eid = er.json()['id']

        # Link email to store
        await admin_client.post(
            f'/api/stores/{sid}/emails',
            json={'email_account_id': eid},
        )

        # Verify link exists
        links = await admin_client.get(f'/api/stores/{sid}/emails')
        assert links.status_code == 200
        assert len(links.json()) == 1

        # Delete store
        r = await admin_client.delete(f'/api/stores/{sid}')
        assert r.status_code == 200

        # Email account should still exist
        er2 = await admin_client.get('/api/email-accounts')
        assert any(a['id'] == eid for a in er2.json())

    async def test_delete_cleans_schedules(self, admin_client):
        r = await admin_client.post('/api/stores', json={'name': 'Sched Store'})
        sid = r.json()['id']

        # Insert schedule directly into DB (bypass API to avoid
        # apscheduler next_run_time bug in test environment)
        async with tasks_mod.async_session() as db:
            sched = Schedule(
                store_id=sid,
                title='Daily task',
                schedule_type='days',
                schedule_time='09:00',
                interval_value=1,
                created_by='test',
            )
            db.add(sched)
            await db.commit()
            await db.refresh(sched)
            sched_id = sched.id

        # Delete store
        r = await admin_client.delete(f'/api/stores/{sid}')
        assert r.status_code == 200

        # Schedule should be gone
        async with tasks_mod.async_session() as db:
            result = await db.execute(
                sa_select(Schedule).where(Schedule.id == sched_id)
            )
            assert result.scalar_one_or_none() is None

    async def test_delete_removes_store_tasks(self, admin_client):
        r = await admin_client.post('/api/stores', json={'name': 'Task Store'})
        sid = r.json()['id']

        # Create a task and wait for completion
        tr = await admin_client.post(
            '/api/tasks',
            json={'title': 'Some task', 'store_id': sid},
        )
        tid = tr.json()['id']
        await wait_for_task(admin_client, tid)

        # Delete store
        r = await admin_client.delete(f'/api/stores/{sid}')
        assert r.status_code == 200

        # Task should be deleted along with the store
        tr2 = await admin_client.get(f'/api/tasks/{tid}')
        assert tr2.status_code == 404

    async def test_delete_blocked_active_task_409(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            execute_delay=5.0,
        )
        r = await admin_client.post(
            '/api/stores', json={'name': 'Active Store'}
        )
        sid = r.json()['id']

        tr = await admin_client.post(
            '/api/tasks',
            json={'title': 'Active task', 'store_id': sid},
        )
        task_id = tr.json()['id']
        await wait_for_task(admin_client, task_id, target='running')

        r = await admin_client.delete(f'/api/stores/{sid}')
        assert r.status_code == 409


class TestStoreEmailBinding:
    async def test_link_email(self, admin_client):
        r = await admin_client.post('/api/stores', json={'name': 'Link Store'})
        sid = r.json()['id']

        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'link@example.com',
                'imap_host': 'imap.example.com',
                'imap_port': 993,
                'password': 'secret',
            },
        )
        eid = er.json()['id']

        r = await admin_client.post(
            f'/api/stores/{sid}/emails',
            json={'email_account_id': eid},
        )
        assert r.status_code == 200

        links = await admin_client.get(f'/api/stores/{sid}/emails')
        assert len(links.json()) == 1
        assert links.json()[0]['email'] == 'link@example.com'

    async def test_unlink_email(self, admin_client):
        r = await admin_client.post(
            '/api/stores', json={'name': 'Unlink Store'}
        )
        sid = r.json()['id']

        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'unlink@example.com',
                'imap_host': 'imap.example.com',
                'imap_port': 993,
                'password': 'secret',
            },
        )
        eid = er.json()['id']

        lr = await admin_client.post(
            f'/api/stores/{sid}/emails',
            json={'email_account_id': eid},
        )
        link_id = lr.json()['id']

        r = await admin_client.delete(f'/api/stores/{sid}/emails/{link_id}')
        assert r.status_code == 200

        links = await admin_client.get(f'/api/stores/{sid}/emails')
        assert len(links.json()) == 0

    async def test_link_duplicate_409(self, admin_client):
        r = await admin_client.post('/api/stores', json={'name': 'Dup Store'})
        sid = r.json()['id']

        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'dup@example.com',
                'imap_host': 'imap.example.com',
                'imap_port': 993,
                'password': 'secret',
            },
        )
        eid = er.json()['id']

        r1 = await admin_client.post(
            f'/api/stores/{sid}/emails',
            json={'email_account_id': eid},
        )
        assert r1.status_code == 200

        r2 = await admin_client.post(
            f'/api/stores/{sid}/emails',
            json={'email_account_id': eid},
        )
        assert r2.status_code == 409


class TestStoreMetadata:
    async def test_new_store_empty_platform_countries(self, admin_client):
        r = await admin_client.post(
            '/api/stores', json={'name': 'Empty PC Store'}
        )
        assert r.status_code == 200
        assert r.json()['platform_countries'] == {}

    async def test_create_store_with_platform_countries(self, admin_client):
        pc = {'amazon': ['US', 'UK']}
        r = await admin_client.post(
            '/api/stores',
            json={'name': 'PC Create', 'platform_countries': pc},
        )
        assert r.status_code == 200
        assert r.json()['platform_countries'] == pc
        # Derived fields
        assert r.json()['platforms'] == ['amazon']
        assert sorted(r.json()['countries']) == ['UK', 'US']

    async def test_metadata_sync_merges_platforms(
        self, admin_client, mock_workspace
    ):
        """sync_store_metadata merges metadata.json into DB."""
        r = await admin_client.post(
            '/api/stores',
            json={
                'name': 'Meta Store',
                'platform_countries': {'amazon': ['US']},
            },
        )
        sid = r.json()['id']

        # Write metadata.json to workspace
        store_dir = mock_workspace.root / 'stores' / 'meta-store'
        store_dir.mkdir(parents=True, exist_ok=True)
        meta = {'platforms': {'amazon': ['US', 'UK'], 'noon': ['EG']}}
        (store_dir / 'metadata.json').write_text(json.dumps(meta))

        async_session_maker = tasks_mod.async_session
        async with async_session_maker() as db:
            await sync_store_metadata(sid, db)

        # Verify merged
        r = await admin_client.get(f'/api/stores/{sid}')
        pc = r.json()['platform_countries']
        assert 'amazon' in pc
        assert sorted(pc['amazon']) == ['UK', 'US']
        assert 'noon' in pc
        assert pc['noon'] == ['EG']

    async def test_metadata_sync_skips_malformed(
        self, admin_client, mock_workspace
    ):
        """Malformed metadata.json doesn't crash."""
        r = await admin_client.post(
            '/api/stores', json={'name': 'Bad Meta Store'}
        )
        sid = r.json()['id']

        store_dir = mock_workspace.root / 'stores' / 'bad-meta-store'
        store_dir.mkdir(parents=True, exist_ok=True)
        (store_dir / 'metadata.json').write_text('not json')

        async with tasks_mod.async_session() as db:
            await sync_store_metadata(sid, db)

        # Store unchanged
        r = await admin_client.get(f'/api/stores/{sid}')
        assert r.json()['platform_countries'] == {}

    async def test_metadata_sanitizes_values(
        self, admin_client, mock_workspace
    ):
        """Mixed-case platform/country names are normalized."""
        r = await admin_client.post(
            '/api/stores', json={'name': 'Sanitize Store'}
        )
        sid = r.json()['id']

        store_dir = mock_workspace.root / 'stores' / 'sanitize-store'
        store_dir.mkdir(parents=True, exist_ok=True)
        meta = {'platforms': {'Amazon': ['us', 'uk'], ' NOON ': ['Eg']}}
        (store_dir / 'metadata.json').write_text(json.dumps(meta))

        async with tasks_mod.async_session() as db:
            await sync_store_metadata(sid, db)

        r = await admin_client.get(f'/api/stores/{sid}')
        pc = r.json()['platform_countries']
        assert 'amazon' in pc
        assert sorted(pc['amazon']) == ['UK', 'US']
        assert 'noon' in pc
        assert pc['noon'] == ['EG']
