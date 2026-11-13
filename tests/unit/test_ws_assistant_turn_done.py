"""Per-turn completion for the workspace assistant (turn end ≠ process end).

The assistant keeps ONE CLI process across turns (stdin stays open),
so ``ws_assistant_done`` — which the stream loop emits when the process
EXITS — never arrives while a healthy session idles between turns.
Every consumer that waits for "this reply is over" needs a per-turn
event: ``ws_assistant_turn_done``, emitted on each ``result``.

Two tiers of proof:

* ``_handle_event`` unit level — a ``result`` emits the result message
  (when non-empty) plus exactly one turn-done, and NOT a done;
* a REAL mock-CLI subprocess attached to a real WorkspaceAgentSession —
  the mock models the real lifecycle (stays alive across turns), so
  this proves the session survives turn 1 (process up, stdin open, no
  ``ws_assistant_done``), answers a second message in the SAME session,
  and only then ends with the process-exit event when stdin closes.
  A mock that exits after its result (the old shape) makes this class
  of bug invisible — that gap is what the subprocess test closes.
"""

import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from app.ai.workspace_assistant import WorkspaceAgentSession
from app.events.bus import event_bus

pytestmark = pytest.mark.unit

MOCK_CLI = str(Path(__file__).resolve().parents[2] / 'tests/e2e/mock_cli.py')


def _bare_session(key: str = '__ws_assistant_utest__') -> WorkspaceAgentSession:
    """A session built without its heavy __init__ — only the result
    branch of ``_handle_event`` is driven."""
    session = WorkspaceAgentSession.__new__(WorkspaceAgentSession)
    session.task_id = key
    session._on_result = None
    return session


async def _drain_until(
    queue: asyncio.Queue,
    type_name: str,
    predicate=None,
    timeout=5.0,
):
    """Pop events until one of ``type_name`` (optionally passing
    ``predicate``) arrives; return (event, everything popped on the
    way). Fails the test on timeout."""
    seen: list[dict] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise AssertionError(
                f'no {type_name!r} event within {timeout}s; seen={seen}'
            )
        raw = await asyncio.wait_for(queue.get(), timeout=remaining)
        data = json.loads(raw)
        if data.get('type') == type_name and (
            predicate is None or predicate(data)
        ):
            return data, seen
        seen.append(data)


def _is_result(data: dict) -> bool:
    return data.get('role') == 'result'


class TestTurnDoneOnResult:
    async def test_a_result_ends_the_turn_not_the_process(self):
        session = _bare_session()
        session.turn_in_flight = True  # a delivered message opened it
        queue = event_bus.subscribe()
        try:
            await session._handle_event({
                'type': 'result',
                'result': 'Here you go.',
            })
            msg, _ = await _drain_until(
                queue, 'ws_assistant_message', _is_result
            )
            assert msg['content'] == 'Here you go.'
            turn, _ = await _drain_until(queue, 'ws_assistant_turn_done')
            assert turn['session_key'] == session.task_id
            # status.running (busy) closed BEFORE the turn-done event:
            # a collector waking on the event must read "not busy".
            assert session.turn_in_flight is False
            # The process-exit event must NOT ride on a turn boundary.
            assert queue.empty()
        finally:
            event_bus.unsubscribe(queue)

    async def test_an_empty_result_still_ends_the_turn(self):
        """Weak models end a turn with an empty result (thinking-only
        turn). The running state must clear for those too — the turn is
        over either way."""
        session = _bare_session()
        queue = event_bus.subscribe()
        try:
            await session._handle_event({'type': 'result', 'result': ''})
            turn, seen = await _drain_until(queue, 'ws_assistant_turn_done')
            assert turn['session_key'] == session.task_id
            assert not [s for s in seen if s['type'] == 'ws_assistant_message']
            assert queue.empty()
        finally:
            event_bus.unsubscribe(queue)

    async def test_every_turn_of_the_session_gets_its_own_done(self):
        session = _bare_session()
        queue = event_bus.subscribe()
        try:
            for text in ('first answer', 'second answer'):
                await session._handle_event({'type': 'result', 'result': text})
                await _drain_until(queue, 'ws_assistant_turn_done')
        finally:
            event_bus.unsubscribe(queue)


class TestLiveSessionAcrossTurns:
    """The bug class, against the real process model.

    Drives a REAL mock-CLI subprocess through the session's own stream
    loop: turn 1 answers, the session STAYS ALIVE (the mock now models
    the real CLI — one process per session, not per turn), a second
    message is answered by the same process, and the process-exit
    event arrives only once stdin closes."""

    async def test_the_session_survives_a_turn_and_answers_again(self):
        session = WorkspaceAgentSession(
            session_key='__ws_assistant_live__',
            prompt='first question',
        )
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            MOCK_CLI,
            '--mode=workspace',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, 'MOCK_CLI_SCENARIO': '{}'},
        )
        session._proc = proc
        queue = event_bus.subscribe()
        streamer = asyncio.create_task(session._stream_output())
        try:
            assert await session.send_user_message('first question')
            # A delivered message opens the turn — status reads busy.
            assert session.turn_in_flight is True

            # Turn 1: result message + per-turn completion.
            msg1, _ = await _drain_until(
                queue, 'ws_assistant_message', _is_result
            )
            assert 'turn 1' in msg1['content']
            turn1, _ = await _drain_until(queue, 'ws_assistant_turn_done')
            assert turn1['session_key'] == session.task_id

            # The session is NOT over: process alive, stdin open, no
            # process-exit event — but the turn IS: busy cleared, so
            # status.running (busy) and status.alive (process) now
            # legitimately differ. (Pre-fix the mock exited here and CI
            # saw the opposite lifecycle — while the real CLI's live
            # session left every "running" consumer stuck.)
            assert proc.returncode is None
            assert session.running is True
            assert session._input_closed is False
            assert session.turn_in_flight is False

            # Second message, SAME process: its own result and its own
            # turn-done.
            assert await session.send_user_message('second question')
            assert session.turn_in_flight is True
            msg2, _ = await _drain_until(
                queue, 'ws_assistant_message', _is_result
            )
            assert 'turn 2' in msg2['content']
            turn2, _ = await _drain_until(queue, 'ws_assistant_turn_done')
            assert turn2['session_key'] == session.task_id
            assert proc.returncode is None
            assert session.turn_in_flight is False

            # Process end is a SEPARATE event, emitted when the
            # session closes: stdin EOF ends the mock, the stream loop
            # then emits ws_assistant_done.
            await session._close_stdin('test_stop')
            done, _ = await _drain_until(queue, 'ws_assistant_done')
            assert done['session_key'] == session.task_id
            assert done['return_code'] == 0
        finally:
            event_bus.unsubscribe(queue)
            if not streamer.done():
                await session._close_stdin('test_cleanup')
            await asyncio.wait_for(streamer, timeout=10)
            if proc.returncode is None:
                proc.kill()
