"""Unit tests for the workspace-assistant manager's reply memory and
send serialization (fix-b / M12).

Two behaviours pinned:

* ``last_reply`` survives the session that produced it — a LATER
  session that errors before answering must not erase the user's
  previous answer (pre-fix, ``last_reply`` read the live session
  object's ``_result_text`` only, so the latest session's emptiness
  shadowed the earlier answer);
* the reply is recorded through the session's ``on_result`` callback
  — the wiring ``_start`` installs — fired on every ``result`` event.

``send_lock`` (the per-user serializer the MCP tool refuses on) is
exercised end-to-end by the workflow test in
tests/workflow/test_wf_review_fix_b.py; here only its dictionary
semantics are pinned.
"""

import asyncio

import pytest

from app.ai.workspace_assistant import (
    WorkspaceAgentSession,
    WorkspaceAssistantManager,
)

pytestmark = pytest.mark.unit


def _session_reporting_to(manager: WorkspaceAssistantManager, key: str):
    """A WorkspaceAgentSession built without its heavy __init__.

    Only ``_handle_event``'s result branch is driven, so the prompt
    building / profile lookup the real constructor does is not needed
    to pin the callback wiring.
    """
    session = WorkspaceAgentSession.__new__(WorkspaceAgentSession)
    session.task_id = key

    def _keep(text: str) -> None:
        manager._last_replies[key] = text

    session._on_result = _keep
    return session


class TestLastReplySurvivesSessions:
    async def test_a_resulting_session_records_the_reply(self):
        manager = WorkspaceAssistantManager()
        session = _session_reporting_to(manager, '__ws_assistant_u1__')
        await session._handle_event({'type': 'result', 'result': 'Done!'})
        assert manager.last_reply('u1') == 'Done!'

    async def test_a_later_silent_session_does_not_erase_it(self):
        """The M12 regression: turn 2 errors before any result event —
        the user's last actual answer must still be reportable."""
        manager = WorkspaceAssistantManager()
        first = _session_reporting_to(manager, '__ws_assistant_u1__')
        await first._handle_event({'type': 'result', 'result': 'First!'})

        # Turn 2's session object exists, produces no result, and is
        # what `is_running`/status would consult — pre-fix,
        # last_reply read THIS object and answered None.
        manager._sessions['__ws_assistant_u1__'] = _session_reporting_to(
            manager, '__ws_assistant_u1__'
        )
        assert manager.last_reply('u1') == 'First!'

    async def test_a_new_result_replaces_the_old_one(self):
        manager = WorkspaceAssistantManager()
        key = '__ws_assistant_u2__'
        for text in ('One', 'Two'):
            session = _session_reporting_to(manager, key)
            await session._handle_event({'type': 'result', 'result': text})
        assert manager.last_reply('u2') == 'Two'

    async def test_clear_history_drops_the_reply_too(self):
        manager = WorkspaceAssistantManager()
        session = _session_reporting_to(manager, '__ws_assistant_u3__')
        await session._handle_event({'type': 'result', 'result': 'Bye'})
        assert manager.last_reply('u3') == 'Bye'
        manager.clear_history('u3')
        assert manager.last_reply('u3') is None


class TestSendLock:
    def test_one_lock_per_user_two_users_do_not_share(self):
        manager = WorkspaceAssistantManager()
        assert manager.send_lock('alice') is not manager.send_lock('bob')

    def test_the_same_user_gets_the_same_lock(self):
        manager = WorkspaceAssistantManager()
        assert manager.send_lock('alice') is manager.send_lock('alice')

    def test_locked_is_visible_to_the_refusing_caller(self):
        manager = WorkspaceAssistantManager()
        lock = manager.send_lock('alice')
        assert lock.locked() is False

        async def _hold_and_check():
            async with lock:
                return lock.locked()

        assert asyncio.run(_hold_and_check()) is True
        assert lock.locked() is False
