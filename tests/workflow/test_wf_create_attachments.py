"""Create-task attachments must reach the agent.

Regression: uploading an image while creating a task stored it in a DB
blob dir invisible to the agent, and the agent auto-started before the
upload landed — so it asked "where is the image?". Now attachments write
into the task workspace ``uploads/`` (agent-visible) and create can defer
the start until the client has uploaded them.
"""

import shutil

import pytest

from app.workspace.manager import VIBE_SELLER_DIR

pytestmark = pytest.mark.workflow

_TASKS_DIR = VIBE_SELLER_DIR / 'tasks'


async def _store(client):
    r = await client.post('/api/stores', json={'name': 'Attach Create Store'})
    assert r.status_code == 200
    return r.json()['id']


async def test_defer_start_leaves_task_pending(admin_client):
    """defer_start=true creates the task but does NOT launch it, so the
    client can upload attachments before the agent reads its prompt."""
    store_id = await _store(admin_client)
    r = await admin_client.post(
        '/api/tasks',
        json={
            'title': 'deferred',
            'store_id': store_id,
            'plan_mode': False,
            'defer_start': True,
        },
    )
    assert r.status_code == 200
    tid = r.json()['id']
    try:
        got = (await admin_client.get(f'/api/tasks/{tid}')).json()
        # Not launched: still PENDING (schedule_or_run was skipped).
        assert got['status'] == 'pending'
    finally:
        shutil.rmtree(_TASKS_DIR / tid, ignore_errors=True)


async def test_attachment_lands_in_agent_workspace(admin_client):
    """Create-time upload is written into tasks/<id>/uploads/ (the agent's
    cwd), not an invisible blob dir."""
    store_id = await _store(admin_client)
    r = await admin_client.post(
        '/api/tasks',
        json={'title': 'with image', 'store_id': store_id, 'defer_start': True},
    )
    tid = r.json()['id']
    task_dir = _TASKS_DIR / tid
    try:
        up = await admin_client.post(
            f'/api/attachments/{tid}',
            files={'file': ('样图 1.png', b'\x89PNG\r\n\x1a\nx', 'image/png')},
        )
        assert up.status_code == 200
        uploads = task_dir / 'uploads'
        saved = [p for p in uploads.iterdir() if p.is_file()]
        assert len(saved) == 1
        assert saved[0].suffix == '.png'
        assert saved[0].read_bytes().startswith(b'\x89PNG')
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)
