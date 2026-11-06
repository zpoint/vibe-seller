"""Hosted-MCP telemetry: one ``mcp_tool_called`` per tool call, and
nothing that was in the arguments — never arguments, task text, ids
or the token."""

import json

import pytest

from app.models.task import Task
from app.telemetry_mcp import mcp_calls_last_24h
from app.telemetry_via import take_answer_via

pytestmark = pytest.mark.workflow

CANARY = 'canary-store-DO-NOT-LEAK'


def _capture(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        'app.telemetry.send',
        lambda event, props=None: events.append((event, dict(props or {}))),
    )
    return events


async def _mint_token(admin_client) -> str:
    r = await admin_client.post('/api/auth/tokens', json={'name': 'mcp'})
    assert r.status_code == 200
    return r.json()['token']


class TestMcpTelemetry:
    async def test_tool_call_emits_one_event_without_arguments(
        self, admin_client, monkeypatch
    ):
        events = _capture(monkeypatch)
        token = await _mint_token(admin_client)
        headers = {'Authorization': f'Bearer {token}'}
        before = mcp_calls_last_24h()

        r = await admin_client.post(
            '/api/mcp',
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {
                    'protocolVersion': '2025-06-18',
                    'clientInfo': {'name': 'claude-code', 'version': '2.1'},
                    'capabilities': {},
                },
            },
            headers=headers,
        )
        assert r.status_code == 200
        r = await admin_client.post(
            '/api/mcp',
            json={
                'jsonrpc': '2.0',
                'id': 2,
                'method': 'tools/call',
                'params': {
                    'name': 'list_tasks',
                    'arguments': {'store_id': CANARY, 'limit': 2},
                },
            },
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()['result']['isError'] is False

        tool_calls = [p for e, p in events if e == 'mcp_tool_called']
        assert len(tool_calls) == 1, events
        props = tool_calls[0]
        assert set(props) == {
            'tool',
            'outcome',
            'duration_ms',
            'user_hash',
            'via',
        }
        assert props['tool'] == 'list_tasks'
        assert props['outcome'] == 'ok'
        assert props['via'] == 'mcp'
        assert type(props['duration_ms']) is int
        assert isinstance(props['user_hash'], str) and props['user_hash']
        blob = json.dumps(props)
        assert 'canary' not in blob
        assert 'store_id' not in blob
        assert 'limit' not in blob
        assert token not in blob

        sessions = [p for e, p in events if e == 'mcp_session_started']
        assert len(sessions) == 1
        assert sessions[0] == {
            'client_name': 'claude-code',
            'protocol_version': '2025-06-18',
        }
        # Token lifecycle is reported too — owner seat + role only.
        made = [p for e, p in events if e == 'api_token_created']
        assert len(made) == 1
        assert set(made[0]) == {'user_hash', 'role'}
        assert mcp_calls_last_24h() == before + 1

    async def test_token_revoke_emits_event(self, admin_client, monkeypatch):
        events = _capture(monkeypatch)
        token = await _mint_token(admin_client)
        tokens = await admin_client.get('/api/auth/tokens')
        token_id = tokens.json()[0]['id']
        r = await admin_client.delete(f'/api/auth/tokens/{token_id}')
        assert r.status_code == 200
        revoked = [p for e, p in events if e == 'api_token_revoked']
        assert len(revoked) == 1
        assert set(revoked[0]) == {'user_hash', 'role'}
        blob = json.dumps(revoked[0])
        assert token not in blob


class TestViaChannel:
    """Task provenance and user-action `via` come from
    the authenticated principal and the request path, never from a
    client field."""

    async def test_task_created_via_ui_vs_mcp(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        events = _capture(monkeypatch)
        # UI surface: cookie-authenticated REST call.
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Via UI task',
                'description': 'Do nothing.',
                'plan_mode': False,
                'defer_start': True,
            },
        )
        assert r.status_code == 200
        # MCP surface: vst_ token over /api/mcp.
        token = await _mint_token(admin_client)
        r = await admin_client.post(
            '/api/mcp',
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {
                    'name': 'create_task',
                    'arguments': {
                        'title': 'Via MCP task',
                        'description': 'Do nothing.',
                        'plan_mode': False,
                    },
                },
            },
            headers={'Authorization': f'Bearer {token}'},
        )
        assert r.status_code == 200
        created = [p for e, p in events if e == 'task_created']
        assert [(p['source'], p['via']) for p in created] == [
            ('ui', 'ui'),
            ('mcp_external', 'mcp'),
        ]

    async def test_question_answer_via_hands_over_per_surface(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        monkeypatch,
    ):
        _capture(monkeypatch)
        token = await _mint_token(admin_client)

        async def park(request_id):
            r = await admin_client.post(
                '/api/tasks',
                json={
                    'title': 'Question task',
                    'description': 'Ask me.',
                    'plan_mode': False,
                    'defer_start': True,
                },
            )
            task_id = r.json()['id']
            async with override_async_session() as db:
                task = await db.get(Task, task_id)
                task.status = 'waiting'
                task.wait_condition = json.dumps({
                    'pending_question': {
                        'request_id': request_id,
                        'questions': [{'question': 'What next?'}],
                    },
                })
                await db.commit()
            return task_id

        task_id = await park('req-ui')
        r = await admin_client.post(
            f'/api/tasks/{task_id}/questions/answer',
            json={
                'request_id': 'req-ui',
                'answers': {'What next?': 'Continue'},
            },
        )
        assert r.status_code == 200
        assert take_answer_via('req-ui') == 'ui'

        task_id = await park('req-mcp')
        r = await admin_client.post(
            '/api/mcp',
            json={
                'jsonrpc': '2.0',
                'id': 2,
                'method': 'tools/call',
                'params': {
                    'name': 'answer_question',
                    'arguments': {
                        'task_id': task_id,
                        'request_id': 'req-mcp',
                        'answers': {'What next?': 'Continue'},
                    },
                },
            },
            headers={'Authorization': f'Bearer {token}'},
        )
        assert r.status_code == 200
        assert take_answer_via('req-mcp') == 'mcp'
