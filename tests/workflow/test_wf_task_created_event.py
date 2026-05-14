"""Workflow tests for the task_created SSE event.

Other tabs (and other users) need to learn about a new task without
polling /api/tasks. This test pins the contract of the event:

- Emitted exactly once per POST /api/tasks (the same tab that posted
  also receives it; the frontend dedupes by id).
- Carries the full TaskResponse payload so the receiver can render
  the row without a follow-up GET.
- Includes store_id at the top level for cheap client-side filtering
  before the receiver decides whether to insert into its current view.
- Fires before any task_update emitted by the background runner, so
  the client's status patches are applied to a row that already exists.
"""

import json

import pytest

from app.events.bus import event_bus

pytestmark = pytest.mark.workflow


def _drain(q):
    out = []
    while not q.empty():
        out.append(json.loads(q.get_nowait()))
    return out


class TestTaskCreatedEvent:
    async def test_store_task_emits_task_created(
        self, admin_client, install_fake_agent
    ):
        r = await admin_client.post(
            '/api/stores', json={'name': 'Live List Store'}
        )
        assert r.status_code == 200
        store_id = r.json()['id']

        q = event_bus.subscribe()
        try:
            r = await admin_client.post(
                '/api/tasks',
                json={
                    'title': 'Cross-tab visible',
                    'plan_mode': False,
                    'store_id': store_id,
                },
            )
            assert r.status_code == 200
            task_id = r.json()['id']

            events = _drain(q)
        finally:
            event_bus.unsubscribe(q)

        created = [e for e in events if e['type'] == 'task_created']
        assert len(created) == 1, (
            f'expected exactly one task_created, got: {events}'
        )
        evt = created[0]
        assert evt['task_id'] == task_id
        assert evt['store_id'] == store_id
        # Full payload so the receiver doesn't need a follow-up GET.
        assert evt['task']['id'] == task_id
        assert evt['task']['title'] == 'Cross-tab visible'
        assert evt['task']['store_id'] == store_id
        assert 'status' in evt['task']
        assert 'created_at' in evt['task']

        # Ordering contract: any task_update for this task must come
        # AFTER the task_created so the client's patch hits an
        # existing row.
        idx_created = next(
            i for i, e in enumerate(events) if e.get('type') == 'task_created'
        )
        for i, e in enumerate(events):
            if e.get('type') == 'task_update' and e.get('task_id') == task_id:
                assert i > idx_created, (
                    'task_update fired before task_created — receiver would '
                    'drop the patch on an empty list'
                )

    async def test_no_store_task_emits_with_null_store_id(
        self, admin_client, install_fake_agent
    ):
        q = event_bus.subscribe()
        try:
            r = await admin_client.post(
                '/api/tasks',
                json={'title': 'Storeless live', 'store_id': None},
            )
            assert r.status_code == 200
            task_id = r.json()['id']
            events = _drain(q)
        finally:
            event_bus.unsubscribe(q)

        created = [e for e in events if e['type'] == 'task_created']
        assert len(created) == 1
        evt = created[0]
        assert evt['task_id'] == task_id
        # Null store_id surfaces explicitly so the "All stores" view
        # can match on it without inferring from missing fields.
        assert evt['store_id'] is None
        assert evt['task']['store_id'] is None
