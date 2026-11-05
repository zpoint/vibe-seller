"""Workflow tests for the MCP image-confirm gate: ``image_request``
and its surface in ``wait_task`` / guidance.

The gate is the vision mock the existing vision tests use
(``VISION_FAKE=1`` — no network, a deterministic PNG). What these
pin:

* ``image_request(get)`` recovers the pending card a task agent's
  proposal parked (the same endpoint the UI calls on reconnect);
* ``wait_task`` wakes with ``reason=needs_operator`` and a
  ``pending.kind=image_confirm`` block, and the guidance names
  ``image_request`` — while the TASK itself stays ``running``;
* ``confirm`` (with the user's edited prompt) and ``cancel`` resolve
  the card through the same route the confirm card posts to;
* the suggested next actions are all accepted in that state (the
  lifecycle-test pattern, driven end to end).
"""

import asyncio
from datetime import UTC, datetime
import json
import shutil
import uuid

import pytest

from app.events.bus import event_bus
from app.models.task import Task
from app.workspace.manager import VIBE_SELLER_DIR

pytestmark = pytest.mark.workflow

MCP_PATH = '/api/mcp'

_TASKS_DIR = VIBE_SELLER_DIR / 'tasks'


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


async def _drain_until(queue, event_type, timeout=5.0):
    async def _loop():
        while True:
            raw = await queue.get()
            data = json.loads(raw)
            if data.get('type') == event_type:
                return data

    return await asyncio.wait_for(_loop(), timeout)


async def _seed_running_task(db_session, task_id: str):
    async with db_session() as db:
        db.add(
            Task(
                id=task_id,
                title='white bg main',
                created_by='system',
                status='running',
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
            )
        )
        await db.commit()


class TestSeeTheCard:
    async def test_get_returns_the_pending_card(
        self, admin_client, monkeypatch
    ):
        monkeypatch.setenv('VISION_FAKE', '1')
        tid = str(uuid.uuid4())
        task_dir = _TASKS_DIR / tid
        queue = event_bus.subscribe()
        try:
            gen = asyncio.create_task(
                admin_client.post(
                    f'/api/tasks/{tid}/image/generate',
                    json={'prompt': 'a main image', 'kind': 'main'},
                )
            )
            await _drain_until(queue, 'image_request')

            card, envelope = await call_tool(
                admin_client,
                'image_request',
                {'task_id': tid, 'action': 'get'},
            )
            assert envelope['isError'] is False
            assert card['pending'] is True
            assert card['request_id']
            assert card['prompt'] == 'a main image'
            assert card['kind'] == 'main'
            assert card['models']

            await call_tool(
                admin_client,
                'image_request',
                {
                    'task_id': tid,
                    'action': 'cancel',
                    'request_id': card['request_id'],
                },
            )
            await asyncio.wait_for(gen, timeout=10)
        finally:
            event_bus.unsubscribe(queue)
            shutil.rmtree(task_dir, ignore_errors=True)

    async def test_get_without_a_card_says_so(self, admin_client):
        empty, _ = await call_tool(
            admin_client,
            'image_request',
            {'task_id': str(uuid.uuid4()), 'action': 'get'},
        )
        assert empty['pending'] is False


class TestWaitTaskSurfacesTheGate:
    async def test_wait_returns_needs_operator_with_the_card(
        self,
        admin_client,
        monkeypatch,
        install_fake_agent,
        override_async_session,
    ):
        monkeypatch.setenv('VISION_FAKE', '1')
        tid = str(uuid.uuid4())
        task_dir = _TASKS_DIR / tid
        await _seed_running_task(override_async_session, tid)
        queue = event_bus.subscribe()
        try:
            gen = asyncio.create_task(
                admin_client.post(
                    f'/api/tasks/{tid}/image/generate',
                    json={'prompt': 'hero banner', 'kind': 'banner'},
                )
            )
            await _drain_until(queue, 'image_request')

            waited, _ = await call_tool(
                admin_client,
                'wait_task',
                {'task_id': tid, 'timeout_seconds': 5},
            )
            # The task itself never left running — the CARD is what
            # needs the operator, and the block says so.
            assert waited['reason'] == 'needs_operator'
            assert waited['status'] == 'running'
            assert waited['pending']['kind'] == 'image_confirm'
            assert waited['pending']['prompt'] == 'hero banner'
            assert waited['pending']['models']
            # The guidance names the tool that resolves the card…
            assert 'image_request' in waited['next_actions']

            # …and every suggested action is accepted in this state.
            _, get_env = await call_tool(
                admin_client,
                'image_request',
                {'task_id': tid, 'action': 'get'},
            )
            assert get_env['isError'] is False
            _, cancel_env = await call_tool(
                admin_client,
                'image_request',
                {'task_id': tid, 'action': 'cancel'},
            )
            assert cancel_env['isError'] is False
            await asyncio.wait_for(gen, timeout=10)
        finally:
            event_bus.unsubscribe(queue)
            shutil.rmtree(task_dir, ignore_errors=True)


class TestConfirmAndCancel:
    async def test_confirm_with_an_edited_prompt(
        self, admin_client, monkeypatch
    ):
        monkeypatch.setenv('VISION_FAKE', '1')
        tid = str(uuid.uuid4())
        task_dir = _TASKS_DIR / tid
        queue = event_bus.subscribe()
        try:
            gen = asyncio.create_task(
                admin_client.post(
                    f'/api/tasks/{tid}/image/generate',
                    json={
                        'prompt': 'original',
                        'model': 'nano-banana-pro-2k',
                        'output_name': 'main.png',
                    },
                )
            )
            await _drain_until(queue, 'image_request')

            # Confirm with the user's edit — the same shape the
            # confirm card posts.
            confirmed, envelope = await call_tool(
                admin_client,
                'image_request',
                {
                    'task_id': tid,
                    'action': 'confirm',
                    'prompt': 'edited by the user',
                    'model': 'nano-banana-pro-2k',
                },
            )
            assert envelope['isError'] is False, envelope
            assert confirmed['ok'] is True

            body = (await asyncio.wait_for(gen, timeout=10)).json()
            assert body['status'] == 'ok'
            # The edit won, exactly as it does through the UI card.
            assert body['prompt'] == 'edited by the user'
            assert (task_dir / 'generated_images' / 'main.png').is_file()
        finally:
            event_bus.unsubscribe(queue)
            shutil.rmtree(task_dir, ignore_errors=True)

    async def test_cancel_refuses_the_proposal(self, admin_client, monkeypatch):
        monkeypatch.setenv('VISION_FAKE', '1')
        tid = str(uuid.uuid4())
        task_dir = _TASKS_DIR / tid
        queue = event_bus.subscribe()
        try:
            gen = asyncio.create_task(
                admin_client.post(
                    f'/api/tasks/{tid}/image/generate',
                    json={'prompt': 'unwanted'},
                )
            )
            await _drain_until(queue, 'image_request')

            cancelled, envelope = await call_tool(
                admin_client,
                'image_request',
                {'task_id': tid, 'action': 'cancel'},
            )
            assert envelope['isError'] is False, envelope
            assert cancelled['action'] == 'cancel'

            body = (await asyncio.wait_for(gen, timeout=10)).json()
            assert body['status'] == 'cancelled'
            assert not (task_dir / 'generated_images').exists()
        finally:
            event_bus.unsubscribe(queue)
            shutil.rmtree(task_dir, ignore_errors=True)

    async def test_confirm_without_a_card_is_an_error(self, admin_client):
        _, envelope = await call_tool(
            admin_client,
            'image_request',
            {'task_id': str(uuid.uuid4()), 'action': 'confirm'},
        )
        assert envelope['isError'] is True
        assert 'no pending image request' in (
            envelope['content'][0]['text'].lower()
        )


class TestGuidanceFlag:
    async def test_get_task_names_image_request_while_parked(
        self,
        admin_client,
        monkeypatch,
        install_fake_agent,
        override_async_session,
    ):
        monkeypatch.setenv('VISION_FAKE', '1')
        tid = str(uuid.uuid4())
        task_dir = _TASKS_DIR / tid
        await _seed_running_task(override_async_session, tid)
        queue = event_bus.subscribe()
        try:
            gen = asyncio.create_task(
                admin_client.post(
                    f'/api/tasks/{tid}/image/generate',
                    json={'prompt': 'parked'},
                )
            )
            await _drain_until(queue, 'image_request')

            detail, _ = await call_tool(
                admin_client, 'get_task', {'task_id': tid}
            )
            assert detail['status'] == 'running'
            assert 'image_request' in detail['next_actions']

            await call_tool(
                admin_client,
                'image_request',
                {'task_id': tid, 'action': 'cancel'},
            )
            await asyncio.wait_for(gen, timeout=10)
        finally:
            event_bus.unsubscribe(queue)
            shutil.rmtree(task_dir, ignore_errors=True)
