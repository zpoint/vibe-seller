"""Create-task attachments must reach the agent.

Regression: uploading an image while creating a task stored it in a DB
blob dir invisible to the agent, and the agent auto-started before the
upload landed — so it asked "where is the image?". Now attachments write
into the task workspace ``uploads/`` (agent-visible) and create can defer
the start until the client has uploaded them.

Second regression, from the fix itself: ``defer_start`` made ``/start``
the resume half of create, but ``/start`` still refused tasks without a
store — so a NO-STORE task created with an attachment was deferred by
create, refused by /start, and stranded at PENDING with no error. Every
test here passed a ``store_id``, which is exactly why that shipped. The
no-store path is now covered, as is the retry that used to delete the
attachments it left behind.
"""

import shutil

import pytest

from app.uploads import MAX_UPLOAD_SIZE
from app.workspace.manager import VIBE_SELLER_DIR

from .conftest import wait_for_task

pytestmark = pytest.mark.workflow

_TASKS_DIR = VIBE_SELLER_DIR / 'tasks'

_PNG = b'\x89PNG\r\n\x1a\nx'


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


async def test_deferred_no_store_task_starts(admin_client):
    """The whole deferred create flow for a task with NO store.

    create(defer_start) → upload → POST /start must LAUNCH it. /start
    used to 400 here ('Cannot start browser task without a store'),
    which had no destination in the client, so the task sat at PENDING
    forever with a `pending` badge and no error anywhere.
    """
    r = await admin_client.post(
        '/api/tasks',
        json={
            'title': 'widget report',
            'description': 'read the attached reference image',
            'store_id': None,
            'defer_start': True,
        },
    )
    assert r.status_code == 200
    tid = r.json()['id']
    task_dir = _TASKS_DIR / tid
    try:
        up = await admin_client.post(
            f'/api/attachments/{tid}',
            files={'file': ('shot.png', _PNG, 'image/png')},
        )
        assert up.status_code == 200

        started = await admin_client.post(f'/api/tasks/{tid}/start')
        assert started.status_code == 200, (
            f'no-store deferred task must be startable; '
            f'got {started.status_code}: {started.text[:200]}'
        )

        # And it must actually move — a 200 that launches nothing is the
        # same stranded task with a nicer status code. No-store tasks are
        # forced into plan mode, so the agent designs and parks at PLANNED.
        got = await wait_for_task(admin_client, tid, target='planned')
        assert got['status'] != 'pending', (
            f'task never left PENDING after /start: {got["status"]}'
        )
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


async def test_retry_keeps_uploaded_attachments(admin_client):
    """Retry wipes run residue but NOT the user's attachments.

    ``reset_task_runtime_state`` rmtree'd the whole workspace, taking
    ``uploads/`` with it while the ``task_attachments`` rows survived —
    so retrying an image task re-created the "where is the image?" bug
    the workspace uploads were introduced to fix, with the DB rows now
    pointing at deleted files.
    """
    store_id = await _store(admin_client)
    r = await admin_client.post(
        '/api/tasks',
        json={'title': 'retry me', 'store_id': store_id, 'defer_start': True},
    )
    tid = r.json()['id']
    task_dir = _TASKS_DIR / tid
    try:
        await admin_client.post(
            f'/api/attachments/{tid}',
            files={'file': ('shot.png', _PNG, 'image/png')},
        )
        # Run residue that a retry MUST clear, alongside the upload.
        (task_dir / 'reviews').mkdir(parents=True, exist_ok=True)
        (task_dir / 'reviews' / 'stale.json').write_text('{}')

        retried = await admin_client.post(f'/api/tasks/{tid}/retry')
        assert retried.status_code == 200, retried.text[:200]

        uploads = task_dir / 'uploads'
        saved = [p for p in uploads.iterdir() if p.is_file()]
        assert [p.name for p in saved] == ['shot.png'], (
            'retry deleted the user-supplied attachment'
        )
        assert saved[0].read_bytes() == _PNG
        assert not (task_dir / 'reviews').exists(), (
            'retry must still clear prior-run residue'
        )
        # The rows that point at the file survive a retry — so must the
        # file, or they dangle.
        rows = await admin_client.get(f'/api/attachments/{tid}')
        assert rows.status_code == 200
        assert len(rows.json()) == 1
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


async def test_a_file_over_the_old_10mb_cap_now_uploads(admin_client):
    """The size a user actually hit.

    A product detail image ("详情图") is routinely 10-30MB; the old
    10MB ceiling refused one at 10.16MB, and the frontend skipped it
    silently, so paste and drag both looked broken. The cap now lives
    in ``app/uploads.py`` and is enforced while streaming.
    """
    store_id = await _store(admin_client)
    r = await admin_client.post(
        '/api/tasks',
        json={'title': 'big image', 'store_id': store_id, 'defer_start': True},
    )
    tid = r.json()['id']
    task_dir = _TASKS_DIR / tid
    # Just past the retired 10MB ceiling, well under the current one.
    big = _PNG + b'\x00' * (12 * 1024 * 1024)
    try:
        up = await admin_client.post(
            f'/api/attachments/{tid}',
            files={'file': ('detail.png', big, 'image/png')},
        )
        assert up.status_code == 200, up.text[:200]
        assert up.json()['file_size'] == len(big)
        saved = [p for p in (task_dir / 'uploads').iterdir() if p.is_file()]
        assert len(saved) == 1
        assert saved[0].stat().st_size == len(big)
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


async def test_an_upload_past_the_cap_is_refused_and_leaves_nothing(
    admin_client,
):
    """Over the cap must 413 AND leave no partial file for the agent."""
    store_id = await _store(admin_client)
    r = await admin_client.post(
        '/api/tasks',
        json={'title': 'too big', 'store_id': store_id, 'defer_start': True},
    )
    tid = r.json()['id']
    task_dir = _TASKS_DIR / tid
    oversize = _PNG + b'\x00' * (MAX_UPLOAD_SIZE + 1)
    try:
        up = await admin_client.post(
            f'/api/attachments/{tid}',
            files={'file': ('huge.png', oversize, 'image/png')},
        )
        assert up.status_code == 413, up.status_code
        uploads = task_dir / 'uploads'
        leftovers = (
            [p for p in uploads.iterdir() if p.is_file()]
            if uploads.exists()
            else []
        )
        assert leftovers == [], f'partial file left behind: {leftovers}'
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)
