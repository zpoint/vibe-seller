"""Workflow tests for the workspace AI assistant."""

import pytest

pytestmark = pytest.mark.workflow


@pytest.fixture
def mock_ws_assistant(monkeypatch):
    """Mock the workspace assistant manager for API tests."""

    class _MockManager:
        def __init__(self):
            self._started = {}
            self._messages = {}

        async def send_or_start(
            self,
            user_id,
            message,
            profile_id='default',
            system_prompt='',
        ):
            self._started[user_id] = True
            self._messages.setdefault(user_id, []).append(message)
            return True

        async def stop(self, user_id):
            if user_id in self._started:
                del self._started[user_id]
                return True
            return False

        def is_running(self, user_id):
            return user_id in self._started

        def clear_history(self, user_id):
            self._messages.pop(user_id, None)

    mock = _MockManager()
    monkeypatch.setattr(
        'app.routers.workspace_assistant.ws_assistant_manager',
        mock,
    )
    return mock


class TestSendMessage:
    async def test_send_message_auto_starts(
        self, admin_client, mock_ws_assistant
    ):
        resp = await admin_client.post(
            '/api/workspace/assistant/message',
            json={'content': 'Hello AI'},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data['ok'] is True

    async def test_empty_message_rejected(
        self, admin_client, mock_ws_assistant
    ):
        resp = await admin_client.post(
            '/api/workspace/assistant/message',
            json={'content': '   '},
        )
        assert resp.status_code == 400

    async def test_concurrent_message_while_running(
        self, admin_client, mock_ws_assistant
    ):
        # First message starts session
        resp1 = await admin_client.post(
            '/api/workspace/assistant/message',
            json={'content': 'First message'},
        )
        assert resp1.status_code == 200

        # Second message goes to running session
        resp2 = await admin_client.post(
            '/api/workspace/assistant/message',
            json={'content': 'Follow up'},
        )
        assert resp2.status_code == 200


class TestStatus:
    async def test_status_not_running(self, admin_client, mock_ws_assistant):
        resp = await admin_client.get('/api/workspace/assistant/status')
        assert resp.status_code == 200
        assert resp.json()['running'] is False

    async def test_status_reflects_running(
        self, admin_client, mock_ws_assistant
    ):
        await admin_client.post(
            '/api/workspace/assistant/message',
            json={'content': 'Start'},
        )
        resp = await admin_client.get('/api/workspace/assistant/status')
        assert resp.status_code == 200
        assert resp.json()['running'] is True


class TestStop:
    async def test_stop_cleans_up(self, admin_client, mock_ws_assistant):
        await admin_client.post(
            '/api/workspace/assistant/message',
            json={'content': 'Start'},
        )
        resp = await admin_client.post('/api/workspace/assistant/stop')
        assert resp.status_code == 200
        assert resp.json()['ok'] is True

        # Verify stopped
        status = await admin_client.get('/api/workspace/assistant/status')
        assert status.json()['running'] is False

    async def test_stop_when_not_running(self, admin_client, mock_ws_assistant):
        resp = await admin_client.post('/api/workspace/assistant/stop')
        assert resp.status_code == 200
        assert resp.json()['ok'] is False


class TestAuth:
    async def test_message_requires_auth(
        self, unauthed_client, mock_ws_assistant
    ):
        resp = await unauthed_client.post(
            '/api/workspace/assistant/message',
            json={'content': 'Hello'},
        )
        assert resp.status_code == 401

    async def test_status_requires_auth(
        self, unauthed_client, mock_ws_assistant
    ):
        resp = await unauthed_client.get('/api/workspace/assistant/status')
        assert resp.status_code == 401

    async def test_stop_requires_auth(self, unauthed_client, mock_ws_assistant):
        resp = await unauthed_client.post('/api/workspace/assistant/stop')
        assert resp.status_code == 401


class TestUserIsolation:
    async def test_different_users_separate_sessions(
        self, admin_client, member_client, mock_ws_assistant
    ):
        # Admin starts a session
        await admin_client.post(
            '/api/workspace/assistant/message',
            json={'content': 'Admin message'},
        )

        # Member checks status — should not be running for them
        resp = await member_client.get('/api/workspace/assistant/status')
        assert resp.json()['running'] is False

        # Member starts their own session
        await member_client.post(
            '/api/workspace/assistant/message',
            json={'content': 'Member message'},
        )

        # Both running
        admin_status = await admin_client.get('/api/workspace/assistant/status')
        member_status = await member_client.get(
            '/api/workspace/assistant/status'
        )
        assert admin_status.json()['running'] is True
        assert member_status.json()['running'] is True
