"""Workflow tests for chat attachments (stage-until-send redesign).

Invariant under test: a user attachment becomes visible to the AGENT
only when the user SENDS the message that carries it. Uploads land in a
staging area OUTSIDE the task workspace (the agent's cwd); the /messages
endpoint promotes them into ``uploads/`` at send time and hands the agent
the absolute paths, while the transcript stores a thumbnail (markdown
image), never the raw path.

Filename handling is exercised for English, Chinese, and
Windows-illegal-character names so the same code path is safe on macOS,
Linux, and Windows.
"""

from pathlib import Path
import shutil
import uuid

import pytest

from app.workspace.manager import VIBE_SELLER_DIR
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow

_TASKS_DIR = VIBE_SELLER_DIR / 'tasks'
_STAGING_DIR = VIBE_SELLER_DIR / 'chat_staging'

_PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 16
# Windows forbids <>:"/\|?* and control chars in filenames.
_WIN_ILLEGAL = '<>:"/\\|?*'


async def _stage(client, task_id, filename, data=_PNG, ctype='image/png'):
    r = await client.post(
        f'/api/tasks/{task_id}/staged',
        files={'file': (filename, data, ctype)},
    )
    return r


async def _create_store_task(client):
    """Create a store + auto task and let it settle (no agent needed)."""
    rs = await client.post('/api/stores', json={'name': 'Attach Store'})
    assert rs.status_code == 200
    store_id = rs.json()['id']
    rt = await client.post(
        '/api/tasks',
        json={'title': 'Attach task', 'plan_mode': False, 'store_id': store_id},
    )
    assert rt.status_code == 200
    return rt.json()['id']


async def test_stage_lands_outside_agent_cwd(admin_client):
    """Staged files are invisible to the agent until Send."""
    tid = str(uuid.uuid4())
    task_dir = _TASKS_DIR / tid
    task_dir.mkdir(parents=True, exist_ok=True)
    staging = _STAGING_DIR / tid
    try:
        r = await _stage(admin_client, tid, 'photo.png')
        assert r.status_code == 200
        body = r.json()
        assert body['filename'] == 'photo.png'
        assert body['content_type'] == 'image/png'
        assert body['url'] == f'/api/tasks/{tid}/staged/{body["id"]}'
        # The response must NOT leak a filesystem path.
        assert 'abs_path' not in body and 'path' not in body
        # File is in staging, NOT anywhere under the agent's cwd.
        assert staging.is_dir()
        staged_bytes = [p for p in staging.rglob('*') if p.is_file()]
        assert len(staged_bytes) == 1
        assert not any(p.is_file() for p in task_dir.rglob('*')), (
            'staged file must not appear in the task workspace before Send'
        )
        # The preview URL serves the bytes back.
        rg = await admin_client.get(body['url'])
        assert rg.status_code == 200
        assert rg.content.startswith(b'\x89PNG')
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)


async def test_stage_english_and_chinese_names_are_ascii_safe(admin_client):
    """Chinese and English uploads both stage to a valid ASCII filename."""
    tid = str(uuid.uuid4())
    staging = _STAGING_DIR / tid
    try:
        for name in ('photo.png', '样图 1.png', '产品主图.png'):
            r = await _stage(admin_client, tid, name)
            assert r.status_code == 200, name
            fn = r.json()['filename']
            assert fn.endswith('.png')
            # ASCII-only → safe for the serve URL and cross-platform paths.
            assert fn.isascii(), fn
            assert fn == fn.encode('ascii', 'ignore').decode()
    finally:
        shutil.rmtree(staging, ignore_errors=True)


async def test_stage_rejects_disallowed_type(admin_client):
    tid = str(uuid.uuid4())
    try:
        r = await _stage(
            admin_client, tid, 'x.sh', data=b'#!/bin/sh', ctype='text/x-sh'
        )
        assert r.status_code == 400
    finally:
        shutil.rmtree(_STAGING_DIR / tid, ignore_errors=True)


async def test_delete_staged_discards_it(admin_client):
    tid = str(uuid.uuid4())
    staging = _STAGING_DIR / tid
    try:
        body = (await _stage(admin_client, tid, 'photo.png')).json()
        rd = await admin_client.delete(f'/api/tasks/{tid}/staged/{body["id"]}')
        assert rd.status_code == 200
        # Gone: serving it now 404s.
        rg = await admin_client.get(body['url'])
        assert rg.status_code == 404
    finally:
        shutil.rmtree(staging, ignore_errors=True)


class TestPromoteOnSend:
    """The /messages endpoint promotes staged files at send time."""

    async def _running_task(self, client, fake):
        tid = await _create_store_task(client)
        await wait_for_task(client, tid)
        # Force the live-agent send path.
        fake._running[tid] = True
        return tid

    async def test_promote_moves_into_workspace_and_splits_content(
        self, admin_client, install_fake_agent
    ):
        tid = await self._running_task(admin_client, install_fake_agent)
        staged = (await _stage(admin_client, tid, 'photo.png')).json()

        r = await admin_client.post(
            f'/api/tasks/{tid}/messages',
            json={'content': 'look at this', 'attachment_ids': [staged['id']]},
        )
        assert r.status_code == 200
        body = r.json()

        # Response (transcript) content: prose + markdown image, NO raw path.
        assert 'look at this' in body['content']
        assert '![' in body['content']
        assert 'Attached file:' not in body['content']
        att = body['attachments'][0]
        assert att['url'] == f'/api/tasks/{tid}/files/uploads/{att["filename"]}'

        # The promoted file now lives in the agent's cwd under uploads/.
        promoted = Path(att['abs_path'])
        assert promoted.is_file()
        assert promoted.parent == (_TASKS_DIR / tid / 'uploads')
        # Staging for this item is consumed.
        assert not (_STAGING_DIR / tid / staged['id']).exists()

        # The AGENT received the absolute path (vision input), not markdown.
        msg = install_fake_agent.get_calls(task_id=tid, action='send_message')
        assert len(msg) == 1
        assert 'Attached file:' in msg[0].message
        assert str(promoted) in msg[0].message
        assert '![' not in msg[0].message

    async def test_attachments_only_no_text(
        self, admin_client, install_fake_agent
    ):
        tid = await self._running_task(admin_client, install_fake_agent)
        staged = (await _stage(admin_client, tid, 'photo.png')).json()
        r = await admin_client.post(
            f'/api/tasks/{tid}/messages',
            json={'content': '', 'attachment_ids': [staged['id']]},
        )
        assert r.status_code == 200
        msg = install_fake_agent.get_calls(task_id=tid, action='send_message')
        assert msg[0].message.startswith('Attached file:')

    async def test_windows_illegal_chars_stripped(
        self, admin_client, install_fake_agent
    ):
        tid = await self._running_task(admin_client, install_fake_agent)
        staged = (
            await _stage(admin_client, tid, f'a{_WIN_ILLEGAL}b.png')
        ).json()
        r = await admin_client.post(
            f'/api/tasks/{tid}/messages',
            json={'content': 'x', 'attachment_ids': [staged['id']]},
        )
        assert r.status_code == 200
        fn = r.json()['attachments'][0]['filename']
        # None of the Windows-forbidden characters survive.
        assert not any(c in fn for c in _WIN_ILLEGAL)
        assert fn.endswith('.png') and len(fn) > len('.png')

    async def test_unknown_attachment_id_is_skipped(
        self, admin_client, install_fake_agent
    ):
        tid = await self._running_task(admin_client, install_fake_agent)
        r = await admin_client.post(
            f'/api/tasks/{tid}/messages',
            json={
                'content': 'hi',
                'attachment_ids': ['not-a-real-id', 'x' * 32],
            },
        )
        assert r.status_code == 200
        assert r.json()['attachments'] == []
        msg = install_fake_agent.get_calls(task_id=tid, action='send_message')
        assert msg[0].message == 'hi'

    async def test_empty_message_without_attachments_rejected(
        self, admin_client, install_fake_agent
    ):
        tid = await self._running_task(admin_client, install_fake_agent)
        r = await admin_client.post(
            f'/api/tasks/{tid}/messages',
            json={'content': '   '},
        )
        assert r.status_code == 400
