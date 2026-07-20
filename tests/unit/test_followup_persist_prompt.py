"""Follow-up user messages must be persisted exactly once.

The conversation router saves the user's message before spawning the
agent; ``AgentSession.start()`` ALSO persisted ``self.prompt`` whenever
``message_history`` was empty — which is exactly the state on the
fresh-session and dead-session-resume follow-up paths, so every
follow-up message landed in ``task_messages`` twice (observed live:
identical user rows ~100ms apart). Ownership is now explicit:
``persist_prompt=False`` on every spawn whose prompt is already in the
DB (follow-ups, retries); True only for initial runs.
"""

from unittest.mock import patch

import pytest

from app.ai.claude_backend import AgentSession
import app.task_runner_followup as followup_mod
from app.task_states import TaskStatus

pytestmark = pytest.mark.unit


class _CaptureManager:
    def __init__(self):
        self.calls = []

    async def run(self, task_id, prompt, **kwargs):
        self.calls.append((task_id, prompt, kwargs))
        return True


async def _noop_finalize(*_a, **_k):
    return None


async def test_spawn_followup_agent_does_not_repersist_prompt():
    mgr = _CaptureManager()
    with (
        patch.object(followup_mod, 'agent_manager', mgr),
        patch.object(
            followup_mod, 'assert_profile_compatible', lambda _pid: None
        ),
        patch.object(followup_mod, 'finalize_followup_session', _noop_finalize),
    ):
        await followup_mod.spawn_followup_agent(
            'task-1',
            None,
            prompt='please also do the other thing',
            system_extra='',
            mode='auto',
            profile_id='default',
            auto_approve_plan=False,
            revert_status=TaskStatus.COMPLETED,
            resume=False,
        )
    assert len(mgr.calls) == 1
    _tid, _prompt, kwargs = mgr.calls[0]
    assert kwargs.get('persist_prompt') is False


def test_agent_session_persist_prompt_default_true():
    s = AgentSession('t', 'p')
    assert s.persist_prompt is True


def test_agent_session_persist_prompt_stored():
    s = AgentSession('t', 'p', persist_prompt=False)
    assert s.persist_prompt is False
