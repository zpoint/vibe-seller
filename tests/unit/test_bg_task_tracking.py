"""Background async-work tracking in the turn contract.

The turn must not end while async work the agent launched is still
running. Two kinds are tracked in ``_async_agents``:

- async **subagents** (Agent/Task tool — "Async agent launched")
- background **shell commands** (Bash/PowerShell auto-background or
  run_in_background — "…background… with ID: <id>")

Both clear on their ``<task-notification>`` completion (attribute OR
element id form). A background command that isn't tracked would be
killed at teardown with no completion record and then poison the next
``--resume`` — this is what these tests pin.
"""

import pytest

from app.ai.claude_backend import AgentSession

pytestmark = pytest.mark.unit


def _session() -> AgentSession:
    return AgentSession(task_id='test-task', prompt='p', mode='execute')


def _tool_result(text: str, tool_use_id: str = 'call_x') -> dict:
    return {
        'type': 'user',
        'message': {
            'role': 'user',
            'content': [
                {
                    'type': 'tool_result',
                    'tool_use_id': tool_use_id,
                    'content': text,
                }
            ],
        },
    }


def _notification(inner: str) -> dict:
    return {
        'type': 'user',
        'message': {
            'role': 'user',
            'content': [{'type': 'text', 'text': inner}],
        },
    }


@pytest.mark.parametrize(
    'phrase',
    [
        'Command moved to the background with ID: bg-1. Output at …',
        'Command was manually backgrounded by user with ID: bg-1',
        'Command running in background with ID: bg-1. Output being written',
    ],
)
def test_bg_shell_launch_is_tracked(phrase):
    """All three Bash/PowerShell background phrasings register the id."""
    s = _session()
    s._track_async_agents(_tool_result(phrase))
    assert 'bg-1' in s._async_agents
    assert s._had_async_spawns is True


def test_bg_shell_completion_clears_element_form():
    """The <task-id>…</task-id> element-form notification clears it."""
    s = _session()
    s._track_async_agents(_tool_result('running in background with ID: bg-1'))
    assert 'bg-1' in s._async_agents
    s._track_async_agents(
        _notification(
            '<task-notification>\n<task-id>bg-1</task-id>\n'
            '<status>completed</status>\n</task-notification>'
        )
    )
    assert 'bg-1' not in s._async_agents


def test_turn_close_blocked_while_bg_shell_running():
    """A running background command holds the turn open, then releases
    on completion (same gate subagents use)."""
    s = _session()
    s._turn_result_seen = True  # clear the earlier 'no_accepted_result' gate
    s._track_async_agents(_tool_result('running in background with ID: bg-1'))
    assert s._turn_close_blocked() == 'async_work_running'
    s._track_async_agents(
        _notification('<task-notification>\n<task-id>bg-1</task-id>')
    )
    # No audit files in the temp workspace → review gate is a no-op, so
    # once the bg command clears the turn is closable.
    assert s._turn_close_blocked() != 'async_work_running'


def test_subagent_launch_still_tracked():
    """Regression: the original async-subagent path is unchanged."""
    s = _session()
    s._agent_spawn_ids = {'spawn-1'}
    s._track_async_agents(
        _tool_result(
            'Async agent launched successfully.\nagentId: agt-9',
            tool_use_id='spawn-1',
        )
    )
    assert s._async_agents.get('spawn-1') == 'agt-9'


def test_reading_or_stopping_a_task_does_not_track():
    """TaskOutput/TaskStop 'found with ID' text must NOT re-track."""
    s = _session()
    s._track_async_agents(_tool_result('No task found with ID: bg-1'))
    assert not s._async_agents


def _spawned_reviewer(s):
    s._agent_spawn_ids = {'spawn-1'}
    s._track_async_agents(
        _tool_result(
            'Async agent launched successfully.\nagentId: agt-9',
            tool_use_id='spawn-1',
        )
    )
    assert s._async_agents.get('spawn-1') == 'agt-9'


_TASK_OUTPUT = (
    '<retrieval_status>success</retrieval_status>\n\n'
    '<task_id>{tid}</task_id>\n\n<task_type>local_agent</task_type>\n\n'
    '<status>{status}</status>\n\n<output>\nStatus: ok\n</output>'
)


def test_task_output_reporting_completed_clears_the_subagent():
    """The CLI's own TaskOutput answer is a completion.

    Observed live: an agent polled its DoD reviewer with TaskOutput and
    got ``<status>completed</status>``; no <task-notification> ever
    reached the stream, so the tracker kept the reviewer "running", the
    gate re-drove the turn five times, then shipped the result as
    UNVERIFIED although the reviewer had written ``Status: ok``.
    """
    s = _session()
    _spawned_reviewer(s)
    s._track_async_agents(
        _tool_result(_TASK_OUTPUT.format(tid='agt-9', status='completed'))
    )
    assert not s._async_agents
    assert s._async_agents_pending_reason() is None


def test_task_output_reporting_running_keeps_it_tracked():
    s = _session()
    _spawned_reviewer(s)
    s._track_async_agents(
        _tool_result(_TASK_OUTPUT.format(tid='agt-9', status='running'))
    )
    assert s._async_agents.get('spawn-1') == 'agt-9'


def test_task_stop_on_a_finished_task_clears_it():
    s = _session()
    _spawned_reviewer(s)
    s._track_async_agents(
        _tool_result(
            '<tool_use_error>Task agt-9 is not running (status: completed)'
            '</tool_use_error>'
        )
    )
    assert not s._async_agents


def test_another_tasks_completion_does_not_clear_this_one():
    s = _session()
    _spawned_reviewer(s)
    s._track_async_agents(
        _tool_result(_TASK_OUTPUT.format(tid='agt-other', status='completed'))
    )
    assert s._async_agents.get('spawn-1') == 'agt-9'


def _system_notification(status: str, **ids) -> dict:
    """The stream's structured completion event (no user message)."""
    return {
        'type': 'system',
        'subtype': 'task_notification',
        'status': status,
        **ids,
    }


def test_the_streams_system_notification_releases_the_subagent():
    """On current CLIs the system event is the ONLY completion record —
    stream-json never echoes the ``<task-notification>`` message (zero in
    whole CI runs). Held until that message, a finished reviewer read as
    "still running" at every result: five gate re-drives, then the turn
    shipped UNVERIFIED with "Done." as its card (observed in CI)."""
    s = _session()
    _spawned_reviewer(s)
    assert 'still running' in s._async_agents_pending_reason()
    s._track_async_agents(
        _system_notification(
            'completed', task_id='agt-9', tool_use_id='spawn-1'
        )
    )
    assert s._async_agents == {}
    assert s._async_agents_pending_reason() is None


@pytest.mark.parametrize('status', ['failed', 'killed', 'stopped'])
def test_any_terminal_status_releases(status):
    s = _session()
    _spawned_reviewer(s)
    s._track_async_agents(_system_notification(status, tool_use_id='spawn-1'))
    assert s._async_agents == {}


def test_a_running_system_notification_keeps_it_tracked():
    s = _session()
    _spawned_reviewer(s)
    s._track_async_agents(_system_notification('running', task_id='agt-9'))
    assert s._async_agents.get('spawn-1') == 'agt-9'


def test_partly_finished_work_still_says_wait():
    s = _session()
    _spawned_reviewer(s)
    s._track_async_agents(_tool_result('running in background with ID: bg-1'))
    s._track_async_agents(
        _system_notification('completed', task_id='agt-9', tool_use_id='x')
    )
    # Only the finished one goes; the background shell still holds the turn.
    assert list(s._async_agents) == ['bg-1']
    assert 'still running' in s._async_agents_pending_reason()
