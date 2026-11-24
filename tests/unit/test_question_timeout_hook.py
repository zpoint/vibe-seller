"""AskUserQuestion on a real AgentSession: bounded wait, park, answer.

The session's own handler is driven (``_handle_ask_user_question``);
only its I/O edges are stubbed — stdin, the event bus, telemetry rows
and ``stop()`` (whose real body would interrupt a CLI process).
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai import claude_backend_hooks as hooks
from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_manager import ClaudeCodeBackend

pytestmark = pytest.mark.unit

QUESTIONS = [
    {
        'question': 'Which shipping option?',
        'header': 'Shipping',
        'options': [{'label': 'Standard'}, {'label': 'Express'}],
        'multiSelect': False,
    }
]


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '0.2')
    emitted: list[tuple] = []

    async def emit(event, payload):
        emitted.append((event, payload))

    monkeypatch.setattr(hooks.event_bus, 'emit', emit)
    asked = AsyncMock(side_effect=lambda task_id: _now())
    answered = AsyncMock()
    monkeypatch.setattr(hooks, 'send_question_asked', asked)
    monkeypatch.setattr(hooks, 'send_question_answered', answered)
    s = AgentSession(task_id='task-q', prompt='p', mode='auto')
    # A live CLI: ``running`` reads the process.
    s._proc = SimpleNamespace(returncode=None)
    s._send_control_response = AsyncMock()
    s._emit_message = AsyncMock()
    s.stop = AsyncMock()
    # Not a scheduled run unless a test says so (no DB in this tier).
    s._question_policy = AsyncMock(return_value='ask')
    s.emitted = emitted
    s.answered_telemetry = answered
    return s


def _now():
    return datetime.now(UTC)


async def _until(predicate, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError('condition never became true')


async def test_unanswered_question_times_out_and_stops_the_agent(session):
    await asyncio.wait_for(
        session._handle_ask_user_question('rid-1', {'questions': QUESTIONS}),
        timeout=2.0,
    )
    park = session._question_park
    assert park is not None
    assert park.request_id == 'rid-1'
    assert park.questions == QUESTIONS
    assert park.waited_s >= 0.2
    await _until(lambda: session.stop.await_count == 1)
    session._send_control_response.assert_not_called()
    assert session._pending_questions == {}
    assert session._answer_events == {}
    session.answered_telemetry.assert_not_called()
    events = [c.args for c in session._emit_message.await_args_list]
    assert any(
        role == 'agent_event' and 'question_parked' in content
        for role, content in events
    )


async def test_answer_before_timeout_is_delivered_in_process(
    session, monkeypatch
):
    monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '5')
    handler = asyncio.create_task(
        session._handle_ask_user_question('rid-2', {'questions': QUESTIONS})
    )
    await _until(lambda: 'rid-2' in session._answer_events)
    assert await session.submit_answer(
        'rid-2', {'Which shipping option?': 'Express'}
    )
    await asyncio.wait_for(handler, 2.0)
    session._send_control_response.assert_awaited_once()
    rid, behavior, updated = session._send_control_response.await_args.args
    assert (rid, behavior) == ('rid-2', 'allow')
    assert updated['answers'] == {'Which shipping option?': 'Express'}
    assert session._question_park is None
    session.stop.assert_not_called()


async def test_second_answer_to_one_question_is_refused(session, monkeypatch):
    """Two answerers (the UI and an external agent over /api/mcp, or two
    tabs) answer one question in the same instant. The first decides;
    the second is refused (the route's 409), never accepted with 200 and
    then silently written over the first."""
    monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '5')
    handler = asyncio.create_task(
        session._handle_ask_user_question('rid-2b', {'questions': QUESTIONS})
    )
    await _until(lambda: 'rid-2b' in session._answer_events)
    first, second = await asyncio.gather(
        session.submit_answer('rid-2b', {'Which shipping option?': 'Express'}),
        session.submit_answer('rid-2b', {'Which shipping option?': 'Standard'}),
    )
    await asyncio.wait_for(handler, 2.0)
    assert (first, second) == (True, False)
    session._send_control_response.assert_awaited_once()
    updated = session._send_control_response.await_args.args[2]
    assert updated['answers'] == {'Which shipping option?': 'Express'}


async def test_answer_in_same_tick_as_timeout_wins(session, monkeypatch):
    async def timed_out_but_answered(evt, timeout_s):
        # The answer landed, then the bound reported a timeout in the
        # same tick: the person did answer, and the agent gets it.
        session._answers['rid-3'] = {'Which shipping option?': 'Standard'}
        return False

    monkeypatch.setattr(hooks, 'wait_for_answer', timed_out_but_answered)
    await session._handle_ask_user_question('rid-3', {'questions': QUESTIONS})
    session._send_control_response.assert_awaited_once()
    assert session._send_control_response.await_args.args[1] == 'allow'
    assert session._question_park is None
    session.stop.assert_not_called()


async def test_submit_answer_after_timeout_is_refused(session):
    await asyncio.wait_for(
        session._handle_ask_user_question('rid-4', {'questions': QUESTIONS}),
        timeout=2.0,
    )
    assert session._question_park is not None
    accepted = await session.submit_answer(
        'rid-4', {'Which shipping option?': 'Express'}
    )
    assert accepted is False
    session._send_control_response.assert_not_called()


async def test_stop_during_wait_does_not_park(session, monkeypatch):
    monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '5')
    handler = asyncio.create_task(
        session._handle_ask_user_question('rid-5', {'questions': QUESTIONS})
    )
    await _until(lambda: 'rid-5' in session._answer_events)
    # What AgentSession.stop() does to a pending question.
    session._stopping = True
    session._answer_events['rid-5'].set()
    await asyncio.wait_for(handler, 2.0)
    # A stop is not an answer: no "answered" telemetry, nothing parked.
    session.answered_telemetry.assert_not_called()
    assert getattr(session, '_question_park', None) is None
    session._send_control_response.assert_not_called()
    session.stop.assert_not_called()


async def test_manager_reports_a_question_being_parked(session):
    """The stall reaper and the answer route ask the MANAGER: a question
    whose wait timed out stays reported until its park is written."""
    manager = ClaudeCodeBackend()
    manager._sessions['task-q'] = session
    await asyncio.wait_for(
        session._handle_ask_user_question('rid-6', {'questions': QUESTIONS}),
        timeout=2.0,
    )
    pending = manager.get_pending_questions('task-q')
    assert pending == {'request_id': 'rid-6', 'questions': QUESTIONS}
    session._question_park.settled.set()
    assert manager.get_pending_questions('task-q') is None


async def test_scheduled_run_question_is_denied_immediately(
    session, monkeypatch
):
    monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '60')
    session._question_policy = AsyncMock(return_value='decline')
    await asyncio.wait_for(
        session._handle_ask_user_question('rid-7', {'questions': QUESTIONS}),
        timeout=0.5,
    )
    session._send_control_response.assert_awaited_once()
    args = session._send_control_response.await_args
    assert args.args[:2] == ('rid-7', 'deny')
    assert 'Nobody is watching this run' in args.kwargs['message']
    assert not [e for e in session.emitted if e[0] == 'task_questions']
    assert session._asked_user_question is False
    assert session._pending_questions == {}
    assert getattr(session, '_question_park', None) is None
    events = [c.args for c in session._emit_message.await_args_list]
    assert any('question_declined' in content for _, content in events)


async def test_non_scheduled_run_still_asks(session, monkeypatch):
    monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '5')
    session._question_policy = AsyncMock(return_value='ask')
    handler = asyncio.create_task(
        session._handle_ask_user_question('rid-8', {'questions': QUESTIONS})
    )
    await _until(lambda: 'rid-8' in session._answer_events)
    assert [e for e in session.emitted if e[0] == 'task_questions']
    session._send_control_response.assert_not_called()
    await session.submit_answer('rid-8', {'Which shipping option?': 'Express'})
    await asyncio.wait_for(handler, 2.0)
