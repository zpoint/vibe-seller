"""Task provenance is server-derived; a body source is ignored."""

import pytest

from app.models.task import Task

pytestmark = pytest.mark.workflow


class TestTaskSourceDerivation:
    async def test_body_source_is_never_trusted(
        self, admin_client, override_async_session, install_fake_agent
    ):
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'spoofed source',
                'source': 'schedule',
                'defer_start': True,
            },
        )
        assert r.status_code in (200, 201), r.text
        async with override_async_session() as db:
            task = await db.get(Task, r.json()['id'])
        assert task.source == 'ui', 'body source must be ignored'

    async def test_parent_task_derives_subtask(
        self, admin_client, override_async_session, install_fake_agent
    ):
        r = await admin_client.post(
            '/api/stores', json={'name': 'Source Derivation Store'}
        )
        assert r.status_code == 200, r.text
        store_id = r.json()['id']

        parent = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'source parent',
                'store_id': store_id,
                'defer_start': True,
            },
        )
        assert parent.status_code in (200, 201), parent.text

        child = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'source child',
                'store_id': store_id,
                'parent_task_id': parent.json()['id'],
                'defer_start': True,
            },
        )
        assert child.status_code in (200, 201), child.text

        async with override_async_session() as db:
            parent_row = await db.get(Task, parent.json()['id'])
            child_row = await db.get(Task, child.json()['id'])
        assert parent_row.source == 'ui'
        assert child_row.source == 'subtask'
