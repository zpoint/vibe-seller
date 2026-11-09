"""The Stop hook ends a turn; it no longer injects a reflection phase.

Knowledge is kept correct during the task (design_system.md), so a stop
with nothing left open is approved at once — no block, no second turn —
for every session, follow-ups and catalog sync included.
"""

import os

os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-testing-only')

from unittest.mock import AsyncMock

import pytest

from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import STOP_CALLBACK

pytestmark = pytest.mark.unit


def _session():
    session = AgentSession(task_id='t-1', prompt='p', mode='auto')
    session._send_hook_response = AsyncMock()
    session._save_result = AsyncMock()
    session._emit_message = AsyncMock()
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_hook_active', [False, True])
async def test_stop_with_nothing_open_is_approved(stop_hook_active):
    session = _session()
    await session._handle_hook_callback(
        'req-1',
        {
            'callback_id': STOP_CALLBACK,
            'input': {'stop_hook_active': stop_hook_active},
        },
    )
    session._send_hook_response.assert_awaited_once_with(
        'req-1', {'decision': 'approve'}
    )
    session._save_result.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'initialize', ['_send_sdk_initialize', '_send_sdk_initialize_auto']
)
async def test_every_session_registers_the_stop_hook(initialize):
    session = _session()
    captured = {}

    async def fake_send(msg, **kw):
        if msg.get('request', {}).get('subtype') == 'initialize':
            captured['hooks'] = msg['request']['hooks']

    session._send_stdin = fake_send
    await getattr(session, initialize)()
    assert captured['hooks']['Stop'] == [{'hookCallbackIds': [STOP_CALLBACK]}]
