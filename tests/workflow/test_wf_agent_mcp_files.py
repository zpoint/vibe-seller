"""Workflow tests for the MCP task-file tools: ``get_task_log``,
``task_files``, and attachments on ``create_task`` / ``send_message``.

Real HTTP → the real route handlers; the agent is the FakeAgent. What
these pin:

* ``get_task_log`` pages the step log and carries the conversation —
  the "debug log" an operator asked for;
* ``task_files`` lists, reads (inline text / URL+blob) and hands out
  the ZIP URL through the SAME routes the UI's file browser uses;
* attachments round-trip: a file sent over MCP lands in the task
  workspace and the AGENT sees it (create: in its system prompt;
  chat: in the delivered message), mirroring the UI's two flows;
* path escapes are refused before any route resolves them.
"""

import asyncio
import base64
from datetime import UTC, datetime
import json
import shutil

import pytest
from sqlalchemy import select

from app.models.task import Task
from app.models.task_step import TaskStep
from app.models.user import User
from app.workspace.manager import VIBE_SELLER_DIR
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

MCP_PATH = '/api/mcp'

_TASKS_DIR = VIBE_SELLER_DIR / 'tasks'

_PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x01\x02fake-png-body' * 8


def _attachment(filename: str, mime: str, data: bytes) -> dict:
    return {
        'filename': filename,
        'mime_type': mime,
        'content_base64': base64.b64encode(data).decode('ascii'),
    }


async def rpc(client, method, params=None, request_id=1):
    payload = {'jsonrpc': '2.0', 'id': request_id, 'method': method}
    if params is not None:
        payload['params'] = params
    return await client.post(MCP_PATH, json=payload)


async def call_tool(client, name, arguments=None):
    r = await rpc(
        client, 'tools/call', {'name': name, 'arguments': arguments or {}}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert 'error' not in body, body
    result = body['result']
    text = result['content'][0]['text']
    if result.get('isError'):
        return {'error': text}, result
    return (json.loads(text) if text else {}), result


async def make_store(client, name='Files Store'):
    r = await client.post('/api/stores', json={'name': name})
    assert r.status_code == 200
    return r.json()['id']


class TestGetTaskLog:
    async def test_log_pages_steps_and_carries_conversation(
        self, admin_client, install_fake_agent, override_async_session
    ):
        store_id = await make_store(admin_client)
        created = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Logged',
                'description': 'Do three things',
                'store_id': store_id,
            },
        )
        task_id = created.json()['id']
        await wait_for_task(admin_client, task_id)
        # The FakeAgent writes results, not browser steps; seed the
        # step log the way the real backend records it.
        async with override_async_session() as db:
            for i in range(7):
                db.add(
                    TaskStep(
                        task_id=task_id,
                        step_index=i,
                        name=f'Step {i}',
                        action_type='browser',
                        status='done',
                        description=f'did thing {i}',
                        result=f'outcome {i}',
                        started_at=datetime.now(UTC).isoformat(),
                    )
                )
            await db.commit()

        data, envelope = await call_tool(
            admin_client, 'get_task_log', {'task_id': task_id}
        )
        assert envelope['isError'] is False
        assert data['task_id'] == task_id
        assert data['status'] == 'completed'
        assert data['steps_total'] == 7
        assert data['steps'][0]['step_index'] == 0
        assert data['steps'][0]['description'] == 'did thing 0'
        assert data['steps'][0]['result'] == 'outcome 0'
        # The conversation is the messages route's own view.
        messages = (
            await admin_client.get(f'/api/tasks/{task_id}/messages')
        ).json()
        assert data['messages_total'] == len(messages)

        # Pagination: the second page is the remaining steps, and an
        # offset past the end is an empty page, honestly reported.
        paged, _ = await call_tool(
            admin_client,
            'get_task_log',
            {'task_id': task_id, 'offset': 5, 'limit': 10},
        )
        assert [s['step_index'] for s in paged['steps']] == [5, 6]
        assert paged['steps_truncated'] is False

        past, _ = await call_tool(
            admin_client,
            'get_task_log',
            {'task_id': task_id, 'offset': 7, 'limit': 10},
        )
        assert past['steps'] == []
        assert past['steps_truncated'] is False

    async def test_log_for_a_missing_task_is_a_tool_error(
        self, admin_client, install_fake_agent
    ):
        _, envelope = await call_tool(
            admin_client, 'get_task_log', {'task_id': 'nope'}
        )
        assert envelope['isError'] is True
        assert 'not found' in envelope['content'][0]['text'].lower()


class TestTaskFiles:
    async def test_list_read_inline_and_zip_url(
        self, admin_client, install_fake_agent
    ):
        store_id = await make_store(admin_client, 'Output Store')
        created = await admin_client.post(
            '/api/tasks',
            json={'title': 'Produces files', 'store_id': store_id},
        )
        task_id = created.json()['id']
        await wait_for_task(admin_client, task_id)

        task_dir = _TASKS_DIR / task_id
        try:
            task_dir.mkdir(parents=True, exist_ok=True)
            (task_dir / 'report.txt').write_text(
                'quarterly numbers\n' * 10, encoding='utf-8'
            )
            (task_dir / 'image.png').write_bytes(_PNG)

            listed, _ = await call_tool(
                admin_client,
                'task_files',
                {'task_id': task_id, 'action': 'list'},
            )
            names = [f['name'] for f in listed['files']]
            assert 'report.txt' in names
            assert 'image.png' in names

            text_read, _ = await call_tool(
                admin_client,
                'task_files',
                {
                    'task_id': task_id,
                    'action': 'read',
                    'path': 'report.txt',
                },
            )
            assert text_read['encoding'] == 'text'
            assert 'quarterly numbers' in text_read['content']

            # A binary answers with a URL and a base64 blob, not text.
            bin_read, _ = await call_tool(
                admin_client,
                'task_files',
                {
                    'task_id': task_id,
                    'action': 'read',
                    'path': 'image.png',
                },
            )
            assert bin_read['encoding'] == 'url'
            assert '/api/tasks/' in bin_read['url']
            decoded = base64.b64decode(bin_read['blob'])
            assert decoded[:8] == b'\x89PNG\r\n\x1a\n'

            # The URL is fetchable with the caller's own session.
            rel = bin_read['url'].split('/api/', 1)[1]
            fetched = await admin_client.get(f'/api/{rel}')
            assert fetched.status_code == 200
            assert fetched.content[:8] == b'\x89PNG\r\n\x1a\n'

            zipped, _ = await call_tool(
                admin_client,
                'task_files',
                {'task_id': task_id, 'action': 'zip_url'},
            )
            assert zipped['url'].endswith(f'/api/tasks/{task_id}/files-zip')
            zip_rel = zipped['url'].split('/api/', 1)[1]
            zip_resp = await admin_client.get(f'/api/{zip_rel}')
            assert zip_resp.status_code == 200
            assert zip_resp.headers['content-type'] == 'application/zip'
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

    async def test_read_refuses_escapes_before_the_route(
        self, admin_client, install_fake_agent
    ):
        store_id = await make_store(admin_client, 'Escape Store')
        created = await admin_client.post(
            '/api/tasks',
            json={'title': 'Escape', 'store_id': store_id},
        )
        task_id = created.json()['id']
        task_dir = _TASKS_DIR / task_id
        try:
            for bad in (
                '../other/report.txt',
                'report/../../report.txt',
                '/etc/passwd',
                'a\\b.png',
            ):
                _, envelope = await call_tool(
                    admin_client,
                    'task_files',
                    {
                        'task_id': task_id,
                        'action': 'read',
                        'path': bad,
                    },
                )
                assert envelope['isError'] is True, bad
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

    async def test_read_without_a_path_is_an_error(
        self, admin_client, install_fake_agent
    ):
        _, envelope = await call_tool(
            admin_client,
            'task_files',
            {'task_id': 'any', 'action': 'read'},
        )
        assert envelope['isError'] is True
        assert 'path' in envelope['content'][0]['text']

    async def test_unknown_action_is_rejected(
        self, admin_client, install_fake_agent
    ):
        """The schema's enum is a promise to the CLIENT; the server
        enforces the action list itself, so a client that ignores the
        enum gets an INVALID_PARAMS error naming the accepted actions."""
        # Refused by the dispatcher's schema validation (INVALID_PARAMS),
        # naming the bad value and the valid choices.
        r = await rpc(
            admin_client,
            'tools/call',
            {
                'name': 'task_files',
                'arguments': {'task_id': 'x', 'action': 'rename'},
            },
        )
        error = r.json()['error']
        assert error['code'] == -32602
        assert 'rename' in error['message']
        assert 'list' in error['message']

    async def test_zip_url_for_a_missing_task_is_the_routes_404(
        self, admin_client, install_fake_agent
    ):
        _, envelope = await call_tool(
            admin_client,
            'task_files',
            {'task_id': 'no-such-task', 'action': 'zip_url'},
        )
        assert envelope['isError'] is True
        assert 'not found' in envelope['content'][0]['text'].lower()


class TestCreateAttachments:
    async def test_attachment_reaches_the_agent(
        self, admin_client, install_fake_agent
    ):
        store_id = await make_store(admin_client, 'Attach Store')
        data, envelope = await call_tool(
            admin_client,
            'create_task',
            {
                'description': 'Describe the attached image',
                'store_id': store_id,
                'attachments': [_attachment('widget.png', 'image/png', _PNG)],
            },
        )
        assert envelope['isError'] is False, envelope
        task_id = data['task_id']
        task_dir = _TASKS_DIR / task_id
        try:
            assert data['attachments'][0]['file_name'] == 'widget.png'
            # The file landed in the task workspace uploads/…
            saved = task_dir / 'uploads' / 'widget.png'
            assert saved.is_file()
            assert saved.read_bytes() == _PNG
            # …BEFORE the agent started, so its prompt names it.
            await wait_for_task(admin_client, task_id)
            runs = install_fake_agent.get_calls(task_id, action='run')
            assert runs, 'the agent never ran'
            assert 'uploads/widget.png' in runs[0].system_extra
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

    async def test_disallowed_type_is_refused_by_the_route(
        self, admin_client, install_fake_agent
    ):
        store_id = await make_store(admin_client, 'Bad Type Store')
        _, envelope = await call_tool(
            admin_client,
            'create_task',
            {
                'description': 'x',
                'store_id': store_id,
                'attachments': [
                    _attachment(
                        'payload.exe',
                        'application/x-msdownload',
                        b'MZ...',
                    )
                ],
            },
        )
        assert envelope['isError'] is True
        assert 'not allowed' in envelope['content'][0]['text'].lower()
        # The refusal happened BEFORE the task was created (the same
        # allow-list the upload route enforces, checked up front), so
        # no PENDING task lingers with a half-delivered attachment.
        listed = await admin_client.get('/api/tasks')
        assert listed.json() == []

    async def test_invalid_base64_is_a_tool_error(
        self, admin_client, install_fake_agent
    ):
        store_id = await make_store(admin_client, 'Bad B64 Store')
        _, envelope = await call_tool(
            admin_client,
            'create_task',
            {
                'description': 'x',
                'store_id': store_id,
                'attachments': [
                    {
                        'filename': 'x.png',
                        'mime_type': 'image/png',
                        'content_base64': 'not base64!!',
                    }
                ],
            },
        )
        assert envelope['isError'] is True
        assert 'base64' in envelope['content'][0]['text'].lower()


class TestChatAttachments:
    async def test_chat_attachment_reaches_the_running_agent(
        self, admin_client, install_fake_agent
    ):
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='later', gate=gate
        )
        store_id = await make_store(admin_client, 'Chat Attach Store')
        created = await admin_client.post(
            '/api/tasks',
            json={'title': 'Chat attach', 'store_id': store_id},
        )
        task_id = created.json()['id']
        task_dir = _TASKS_DIR / task_id
        try:
            await install_fake_agent.wait_started(task_id)
            sent, envelope = await call_tool(
                admin_client,
                'send_message',
                {
                    'task_id': task_id,
                    'message': 'Use the attached reference',
                    'attachments': [_attachment('ref.png', 'image/png', _PNG)],
                },
            )
            assert envelope['isError'] is False, envelope
            assert sent['attachments'], sent
            promoted = sent['attachments'][0]
            assert promoted['filename'] == 'ref.png'
            # The agent's copy of the message names the absolute path.
            delivered = install_fake_agent.get_calls(
                task_id, action='send_message'
            )
            assert delivered
            assert 'Attached file:' in delivered[-1].message
            assert promoted['filename'] in delivered[-1].message
            gate.set()
            await wait_for_task(admin_client, task_id)
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

    async def test_attachments_only_message_is_delivered(
        self, admin_client, install_fake_agent
    ):
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='later', gate=gate
        )
        store_id = await make_store(admin_client, 'No Text Store')
        created = await admin_client.post(
            '/api/tasks',
            json={'title': 'No text', 'store_id': store_id},
        )
        task_id = created.json()['id']
        task_dir = _TASKS_DIR / task_id
        try:
            await install_fake_agent.wait_started(task_id)
            _, envelope = await call_tool(
                admin_client,
                'send_message',
                {
                    'task_id': task_id,
                    'attachments': [
                        _attachment('chart.png', 'image/png', _PNG)
                    ],
                },
            )
            assert envelope['isError'] is False, envelope
            gate.set()
            await wait_for_task(admin_client, task_id)
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)


class TestMemberParity:
    """No A-row route restricts by role — a member drives the same
    routes a admin does (they see the same tasks they always see)."""

    async def test_member_can_list_and_log(
        self, member_client, install_fake_agent, override_async_session
    ):
        async with override_async_session() as db:
            admin = (await db.execute(select(User.id).limit(1))).scalar_one()
            db.add(
                Task(
                    id='log-parity-1',
                    title='parity',
                    created_by=admin,
                    status='completed',
                    created_at=datetime.now(UTC).isoformat(),
                    updated_at=datetime.now(UTC).isoformat(),
                )
            )
            await db.commit()

        listed, _ = await call_tool(
            member_client,
            'task_files',
            {'task_id': 'log-parity-1', 'action': 'list'},
        )
        assert listed == {'task_id': 'log-parity-1', 'files': []}

        log, envelope = await call_tool(
            member_client, 'get_task_log', {'task_id': 'log-parity-1'}
        )
        assert envelope['isError'] is False
        assert log['steps_total'] == 0


class TestUnauthenticated:
    async def test_files_tools_require_auth(self, unauthed_client, admin_user):
        r = await unauthed_client.post(
            MCP_PATH,
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {
                    'name': 'task_files',
                    'arguments': {'task_id': 'x', 'action': 'list'},
                },
            },
        )
        assert r.status_code == 401
        assert r.headers.get('www-authenticate') == 'Bearer'
