"""Unit tests for result-event turn semantics in AgentSession.

A CLI process hosts MANY turns (initial turn, gate redrives,
task-notification continuations, injected follow-ups). Every ACCEPTED
execute-phase result is a turn boundary and emits its own
role='result' card; ``_result_text`` tracks the LAST non-empty one —
the process's final word is the deliverable.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import (
    AUTO_APPROVE_CALLBACK,
    REVIEW_REDRIVE_MAX,
)
from app.ai.review_redrive import RedriveClock

pytestmark = pytest.mark.unit


class _FakeClock:
    """Hand-cranked monotonic source for the re-drive budget."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float):
        self.t += seconds


def _make_session(mode: str = 'execute') -> AgentSession:
    return AgentSession(
        task_id='test-task',
        prompt='test',
        mode=mode,
    )


class TestHandleEventResultTurns:
    """Per-turn result cards, last-result-wins deliverable."""

    async def test_first_result_emitted_as_result(self):
        """First result during execution → role='result'."""
        session = _make_session('execute')
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            await session._handle_event({
                'type': 'result',
                'result': 'Big result',
            })

        assert len(emitted) == 1
        assert emitted[0] == ('result', 'Big result')
        assert session._result_text == 'Big result'
        assert session._turn_result_seen is True

    async def test_each_nonempty_result_is_a_turn_card_last_wins(self):
        """Later results (task-notification continuations) are their
        own turn boundaries — each gets a card, and the LAST non-empty
        text becomes the deliverable."""
        session = _make_session('execute')
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            await session._handle_event({
                'type': 'result',
                'result': 'First big result',
            })
            await session._handle_event({'type': 'result', 'result': 'Ack 1'})
            await session._handle_event({'type': 'result', 'result': 'Ack 2'})

        assert emitted == [
            ('result', 'First big result'),
            ('result', 'Ack 1'),
            ('result', 'Ack 2'),
        ]
        assert session._result_text == 'Ack 2'

    async def test_planning_phase_results_demoted(self):
        """Results during planning phase → always 'assistant'."""
        session = _make_session('plan_then_execute')
        assert session._executing is False
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            await session._handle_event({
                'type': 'result',
                'result': 'Plan summary',
            })

        assert len(emitted) == 1
        assert emitted[0] == ('assistant', 'Plan summary')
        assert session._result_text == ''
        assert session._turn_result_seen is False

    async def test_execution_results_after_planning(self):
        """After planning, every execution result is a turn card."""
        session = _make_session('plan_then_execute')
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            # Planning phase result
            await session._handle_event({
                'type': 'result',
                'result': 'Plan done',
            })
            # Simulate plan approval → execution starts
            session._executing = True
            session._plan_saved = True
            # Execution result
            await session._handle_event({
                'type': 'result',
                'result': 'Execution done',
            })
            # Notification-continuation result
            await session._handle_event({
                'type': 'result',
                'result': 'Background ack',
            })

        assert emitted[0] == ('assistant', 'Plan done')
        assert emitted[1] == ('result', 'Execution done')
        assert emitted[2] == ('result', 'Background ack')
        assert session._result_text == 'Background ack'

    async def test_error_then_success_ships_success(self):
        """An intermediate error a later turn recovered from must not
        ship FAILED — the LAST accepted result's error state wins."""
        session = _make_session('execute')

        async def _noop(*a, **k):
            pass

        with patch.object(session, '_emit_message', _noop):
            await session._handle_event({
                'type': 'result',
                'result': 'Error occurred',
                'is_error': True,
            })
            assert session._last_result_is_error is True
            await session._handle_event({
                'type': 'result',
                'result': 'Recovered fine',
            })

        assert session._last_result_is_error is False
        # The exit-time fold never fired an error.
        assert session._is_error_result is False
        assert session._result_text == 'Recovered fine'

    async def test_success_then_error_marks_error(self):
        session = _make_session('execute')

        async def _noop(*a, **k):
            pass

        with patch.object(session, '_emit_message', _noop):
            await session._handle_event({
                'type': 'result',
                'result': 'Looked good',
            })
            await session._handle_event({
                'type': 'result',
                'result': 'Late failure',
                'is_error': True,
            })

        assert session._last_result_is_error is True

    async def test_forced_error_survives_later_success(self):
        """A forced error (circuit breaker, rc!=0) is sticky — a
        benign last result must not clear it."""
        session = _make_session('execute')
        session._is_error_result = True  # e.g. circuit breaker fired

        async def _noop(*a, **k):
            pass

        with patch.object(session, '_emit_message', _noop):
            await session._handle_event({
                'type': 'result',
                'result': 'All done, honest',
            })

        assert session._is_error_result is True
        assert session._last_result_is_error is False

    async def test_empty_first_execute_result_still_emits_turn_end(self):
        """First execute-phase result emits even when text is empty.

        The result event is the canonical end-of-turn signal that
        chat-mode follow-up sessions, UI polling, and e2e tests rely
        on. Weaker models (GLM-4.7 observed) sometimes emit the
        final answer in a ``thinking`` block only, leaving the
        result text empty — the turn-end signal must still fire.
        """
        session = _make_session('execute')
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            await session._handle_event({'type': 'result', 'result': ''})

        assert emitted == [('result', '')]
        assert session._turn_result_seen is True

    async def test_empty_subsequent_result_dropped(self):
        """Within a turn, a trailing empty result carries no
        information — dropped, and it never clears the deliverable."""
        session = _make_session('execute')
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            await session._handle_event({
                'type': 'result',
                'result': 'First',
            })
            await session._handle_event({'type': 'result', 'result': ''})

        assert emitted == [('result', 'First')]
        assert session._result_text == 'First'

    async def test_empty_first_of_new_turn_emits_but_keeps_deliverable(
        self,
    ):
        """A follow-up injection opens a new turn; its empty turn-end
        signal still emits a card but must NOT clear the prior turn's
        deliverable text."""
        session = _make_session('execute')
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            await session._handle_event({
                'type': 'result',
                'result': 'Turn one deliverable',
            })
            # Simulate the injection reset send_user_message performs.
            session._turn_result_seen = False
            await session._handle_event({'type': 'result', 'result': ''})

        assert emitted == [
            ('result', 'Turn one deliverable'),
            ('result', ''),
        ]
        assert session._result_text == 'Turn one deliverable'

    async def test_empty_planning_phase_result_dropped(self):
        """Empty planning-phase result is dropped — no end-of-turn
        signal needed during planning."""
        session = _make_session('plan_then_execute')
        assert session._executing is False
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            await session._handle_event({'type': 'result', 'result': ''})

        assert emitted == []
        assert session._turn_result_seen is False

    async def test_auto_mode_first_result(self):
        """Auto mode behaves same as execute."""
        session = _make_session('auto')
        assert session._executing is True
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            await session._handle_event({
                'type': 'result',
                'result': 'Auto result',
            })
            await session._handle_event({
                'type': 'result',
                'result': 'Extra ack',
            })

        assert emitted[0] == ('result', 'Auto result')
        assert emitted[1] == ('result', 'Extra ack')
        assert session._result_text == 'Extra ack'

    async def test_legacy_linger_zero_closes_at_result(self, monkeypatch):
        """With linger=0 (the escape hatch) the turn terminator
        still fires inline at the accepted result — byte-identical to
        the pre-migration close-at-result behavior."""
        monkeypatch.setenv('VIBE_TURN_LINGER_S', '0')
        monkeypatch.setenv('VIBE_TURN_LINGER_QUIET_S', '0')
        session = _make_session('execute')

        async def _noop(*a, **k):
            pass

        with patch.object(session, '_emit_message', _noop):
            await session._handle_event({
                'type': 'result',
                'result': 'done',
            })
        assert session._input_closed is True

    async def test_linger_configured_keeps_channel_open(self):
        """With a linger window, the result event does NOT close
        stdin — the quiescence watchdog owns termination."""
        session = _make_session('execute')

        async def _noop(*a, **k):
            pass

        with (
            patch.object(session, '_emit_message', _noop),
            patch.object(session, '_turn_linger_seconds', return_value=5.0),
        ):
            await session._handle_event({
                'type': 'result',
                'result': 'done',
            })
        assert session._input_closed is False


class TestReviewGateRedrive:
    """A result while the turn's own work is still open (here: a listing
    batch uploaded without a clean verdict) must NOT end the turn: keep
    the control channel open and re-drive the agent instead of closing
    stdin (which would silently deny the tool calls that finish it). See
    app/ai/claude_backend_stream result branch."""

    async def test_unsatisfied_gate_redrives_instead_of_emitting(self):
        session = _make_session('auto')
        session.task_dir = '/tmp/fake-task'
        emitted: list[tuple[str, str]] = []
        sent: list[str] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        async def _mock_send(msg):
            sent.append(msg)

        with (
            patch.object(session, '_emit_message', _mock_emit),
            patch.object(session, 'send_user_message', _mock_send),
            patch(
                'app.ai.claude_backend_review_gate.check_upload_verdicts',
                return_value='Batch 100000000001 has no parse-feedback verdict.',
            ),
        ):
            await session._handle_event({
                'type': 'result',
                'result': 'placeholder — reviewer running in background',
            })

        # The placeholder result was NOT finalized as the turn's result.
        assert session._turn_result_seen is False
        assert session._result_text == ''
        # The agent was re-driven with the gate's deny reason.
        assert len(sent) == 1
        assert 'no parse-feedback verdict' in sent[0]
        assert session._review_redrive_count == 1
        # A structured re-drive marker was emitted (not the result card).
        assert any(r == 'agent_event' for r, _ in emitted)
        assert not any(r == 'result' for r, _ in emitted)

    async def test_redrive_is_bounded(self):
        """After REVIEW_REDRIVE_MAX re-drives, the turn is allowed to end
        so an unsatisfiable gate can't wedge the session forever."""
        session = _make_session('auto')
        session.task_dir = '/tmp/fake-task'
        for _ in range(REVIEW_REDRIVE_MAX):
            session._note_review_redrive()
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with (
            patch.object(session, '_emit_message', _mock_emit),
            patch(
                'app.ai.claude_backend_review_gate.check_upload_verdicts',
                return_value='still gaps',
            ),
        ):
            await session._handle_event({
                'type': 'result',
                'result': 'Final answer',
            })

        # Bound hit → the turn still ends (no wedge), but the result is
        # banner-marked so open work is never mistaken for finished work.
        roles = [r for r, _ in emitted]
        assert roles == ['agent_event', 'result']
        # The fail-open is on the record. It was diagnosed once from a
        # single log line; a transcript event is what makes the next
        # post-mortem possible without server logs.
        assert 'review_gate_failed_open' in emitted[0][1]
        content = emitted[1][1]
        assert content.endswith('Final answer')
        assert 'Ended with open work' in content and 'still gaps' in content
        assert session._turn_result_seen is True
        # …and the turn is now TERMINAL. See
        # test_failed_open_turn_stops_waiting_on_async_work.
        assert session._review_gate_failed_open is True

    async def test_the_clock_can_exhaust_the_budget_before_the_attempts(self):
        """A gate may run out of TIME with attempts to spare.

        The defect: five attempts at 100-150s/turn outlast any deadline
        a caller holds a run open for, so the gate spent the whole clock
        and the run was killed holding a finished result. One re-drive
        has happened here — four attempts left — but the evidence says
        the next turn cannot fit, so the gate fails open now, while
        there is still time to land COMPLETED.
        """
        session = _make_session('auto')
        session.task_dir = '/tmp/fake-task'
        clock = _FakeClock()
        session._review_redrive_clock = RedriveClock(300, now=clock)
        session._review_redrive_clock.note_redrive()
        clock.advance(290)
        emitted: list[tuple[str, str]] = []
        sent: list[str] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        async def _mock_send(msg):
            sent.append(msg)

        with (
            patch.object(session, '_emit_message', _mock_emit),
            patch.object(session, 'send_user_message', _mock_send),
            patch(
                'app.ai.claude_backend_review_gate.check_upload_verdicts',
                return_value='still gaps',
            ),
        ):
            await session._handle_event({
                'type': 'result',
                'result': 'Final answer',
            })

        assert session._review_redrive_count < REVIEW_REDRIVE_MAX, (
            'attempts were NOT the binding constraint — that is the point'
        )
        assert sent == [], 'no further re-drive was issued'
        assert session._review_gate_failed_open is True
        assert 'Ended with open work' in emitted[-1][1]

    async def test_satisfied_gate_ends_turn_normally(self):
        """Gate satisfied (helpers return None) → result finalizes."""
        session = _make_session('auto')
        session.task_dir = '/tmp/fake-task'
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with (
            patch.object(session, '_emit_message', _mock_emit),
            patch(
                'app.ai.claude_backend_review_gate.check_upload_verdicts',
                return_value=None,
            ),
        ):
            await session._handle_event({
                'type': 'result',
                'result': 'Verified answer',
            })

        assert emitted == [('result', 'Verified answer')]
        assert session._turn_result_seen is True
        assert session._review_redrive_count == 0

    # ── The core regression: the control channel must stay OPEN while
    # the turn's work is open. This is the exact invariant the
    # production bug violated — a `result` closed stdin, so the remaining
    # tool calls (and the in-place fixes) lost their approval channel
    # and the CLI default-denied all of them. Neither
    # the workflow tests (FakeAgent replaces AgentSession, never touches
    # stdin) nor any e2e test covered this surface. These two pins do.

    async def test_unsatisfied_gate_keeps_control_channel_open(self):
        """Unsatisfied gate → stdin is NOT closed (approvals survive)."""
        session = _make_session('auto')
        session.task_dir = '/tmp/fake-task'
        session._proc = Mock()
        session._proc.stdin = Mock()

        async def _noop(*a, **k):
            pass

        with (
            patch.object(session, '_emit_message', _noop),
            patch.object(session, 'send_user_message', _noop),
            patch(
                'app.ai.claude_backend_review_gate.check_upload_verdicts',
                return_value='Batch 100000000001 has no parse-feedback verdict.',
            ),
        ):
            await session._handle_event({
                'type': 'result',
                'result': 'placeholder',
            })

        # The bug: these would be True / called. The fix keeps them clean.
        assert session._input_closed is False
        session._proc.stdin.close.assert_not_called()

    async def test_satisfied_gate_closes_control_channel(self, monkeypatch):
        """Gate satisfied + linger=0 (escape hatch) → stdin DOES close
        so the CLI exits — the legacy end-of-turn must not regress.
        (Under the active default tiers the quiescence watchdog owns
        the close instead; see test_linger_configured_keeps_channel_
        open.)"""
        monkeypatch.setenv('VIBE_TURN_LINGER_S', '0')
        monkeypatch.setenv('VIBE_TURN_LINGER_QUIET_S', '0')
        session = _make_session('auto')
        session.task_dir = '/tmp/fake-task'
        session._proc = Mock()
        session._proc.stdin = Mock()

        async def _noop(*a, **k):
            pass

        with (
            patch.object(session, '_emit_message', _noop),
            patch(
                'app.ai.claude_backend_review_gate.check_upload_verdicts',
                return_value=None,
            ),
        ):
            await session._handle_event({
                'type': 'result',
                'result': 'Verified answer',
            })

        assert session._input_closed is True
        session._proc.stdin.close.assert_called_once()


class TestResultTextIsTheAnswer:
    """With no post-task reflection turn, the result event's own text is
    the user-facing answer — nothing replaces or suppresses it."""

    async def test_result_text_passes_through(self):
        session = _make_session('execute')
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            await session._handle_event({
                'type': 'result',
                'result': 'Plain answer',
            })

        assert emitted == [('result', 'Plain answer')]
        assert session._result_text == 'Plain answer'


class TestAsyncAgentTurnHold:
    """A `result` while async subagents launched this turn are still
    running must NOT end the turn — the CLI emits its result as soon as
    the MAIN agent stops, and closing stdin then default-denies every
    remaining subagent tool call (observed live: a DoD reviewer died
    mid-verification while the shipped result claimed it was 'running
    in the background')."""

    def _spawn_async_agent(self, session, agent_id='agent-abc123'):
        """Feed the spawn tool_use + async launch ack into the stream."""
        return [
            {
                'type': 'assistant',
                'parent_tool_use_id': None,
                'message': {
                    'content': [
                        {
                            'type': 'tool_use',
                            'id': 'toolu_spawn1',
                            'name': 'Agent',
                            'input': {'prompt': 'DoD review the upload'},
                        }
                    ]
                },
            },
            {
                'type': 'user',
                'parent_tool_use_id': None,
                'message': {
                    'content': [
                        {
                            'type': 'tool_result',
                            'tool_use_id': 'toolu_spawn1',
                            'content': (
                                'Async agent launched successfully. '
                                '(internal metadata)\n'
                                f'agentId: {agent_id}'
                            ),
                        }
                    ]
                },
            },
        ]

    async def test_result_with_running_async_agent_redrives(self):
        session = _make_session('auto')
        session.task_dir = '/tmp/fake-task'
        emitted, sent = [], []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        async def _mock_send(msg):
            sent.append(msg)

        with (
            patch.object(session, '_emit_message', _mock_emit),
            patch.object(session, 'send_user_message', _mock_send),
            patch(
                'app.ai.claude_backend_review_gate.check_upload_verdicts',
                return_value=None,
            ),
        ):
            for ev in self._spawn_async_agent(session):
                await session._handle_event(ev)
            await session._handle_event({
                'type': 'result',
                'result': 'reviewer is running in the background',
            })

        assert session._turn_result_seen is False
        assert len(sent) == 1 and 'still running' in sent[0]
        assert session._review_redrive_count == 1

    async def test_sync_spawn_does_not_hold_turn(self):
        # A sync Task tool blocks the main agent; its tool_result is the
        # subagent's ANSWER, not a launch ack — no turn hold.
        session = _make_session('auto')
        session.task_dir = '/tmp/fake-task'

        async def _noop(*a, **k):
            pass

        with (
            patch.object(session, '_emit_message', _noop),
            patch(
                'app.ai.claude_backend_review_gate.check_upload_verdicts',
                return_value=None,
            ),
        ):
            events = self._spawn_async_agent(session)
            events[1]['message']['content'][0]['content'] = (
                'Review complete: Status ok, verdict written.'
            )
            for ev in events:
                await session._handle_event(ev)
            await session._handle_event({
                'type': 'result',
                'result': 'done',
            })

        assert session._async_agents == {}
        assert session._turn_result_seen is True

    async def test_task_notification_releases_turn(self):
        session = _make_session('auto')
        session.task_dir = '/tmp/fake-task'

        async def _noop(*a, **k):
            pass

        with (
            patch.object(session, '_emit_message', _noop),
            patch(
                'app.ai.claude_backend_review_gate.check_upload_verdicts',
                return_value=None,
            ),
        ):
            for ev in self._spawn_async_agent(session):
                await session._handle_event(ev)
            assert session._async_agents
            await session._handle_event({
                'type': 'user',
                'message': {
                    'content': [
                        {
                            'type': 'text',
                            'text': (
                                '<task-notification tool-use-id='
                                '"toolu_spawn1" status="completed">'
                                'done</task-notification>'
                            ),
                        }
                    ]
                },
            })
            assert session._async_agents == {}
            await session._handle_event({
                'type': 'result',
                'result': 'final answer',
            })

        assert session._turn_result_seen is True

    async def test_unattributable_notification_fails_open(self):
        # A notification whose ids we can't match must clear the set —
        # a format change may never wedge the turn forever.
        session = _make_session('auto')

        async def _noop(*a, **k):
            pass

        with patch.object(session, '_emit_message', _noop):
            for ev in self._spawn_async_agent(session):
                await session._handle_event(ev)
            assert session._async_agents
            await session._handle_event({
                'type': 'user',
                'message': {
                    'content': [
                        {
                            'type': 'text',
                            'text': '<task-notification>done</task-notification>',
                        }
                    ]
                },
            })
        assert session._async_agents == {}

    async def test_notification_by_agent_id_matches(self):
        session = _make_session('auto')

        async def _noop(*a, **k):
            pass

        with patch.object(session, '_emit_message', _noop):
            for ev in self._spawn_async_agent(session, agent_id='ag-77'):
                await session._handle_event(ev)
            await session._handle_event({
                'type': 'user',
                'message': {
                    'content': [
                        {
                            'type': 'text',
                            'text': (
                                '<task-notification agent-id="ag-77">'
                                'finished</task-notification>'
                            ),
                        }
                    ]
                },
            })
        assert session._async_agents == {}


class TestSubagentWritesKeepTheHold:
    """A running async subagent's own file writes never release its hold
    on the turn — only its completion notification does."""

    def _write_event(self, parent, path):
        return {
            'type': 'assistant',
            'parent_tool_use_id': parent,
            'message': {
                'content': [
                    {
                        'type': 'tool_use',
                        'id': 'toolu_w1',
                        'name': 'Write',
                        'input': {'file_path': f'/ws/{path}'},
                    }
                ]
            },
        }

    async def test_writes_keep_the_hold(self):
        session = _make_session('auto')
        session._async_agents = {'toolu_spawn1': 'agent-r'}

        async def _noop(*a, **k):
            pass

        with patch.object(session, '_emit_message', _noop):
            await session._handle_event(
                self._write_event('toolu_spawn1', 'notes.md')
            )
            await session._handle_event(
                self._write_event('toolu_spawn1', 'REVIEW_2026-07-20_iter1.md')
            )
            await session._handle_event(self._write_event(None, 'out.md'))
        assert session._async_agents == {'toolu_spawn1': 'agent-r'}


class TestEmptyUnterminatedTurn:
    """A response that stopped with no stop reason, no text and no action
    is a stream that ended early. Observed in CI: "save this as a skill"
    got a lone thinking block, stop_reason "", and the turn closed as
    success with nothing done."""

    async def test_it_is_continued_once(self):
        session = _make_session('execute')
        sent: list[str] = []

        async def _noop(*a, **k):
            pass

        async def _send(msg):
            sent.append(msg)

        empty = {'type': 'result', 'result': '', 'stop_reason': ''}
        with (
            patch.object(session, '_emit_message', _noop),
            patch.object(session, 'send_user_message', _send),
        ):
            await session._handle_event(dict(empty))
            assert len(sent) == 1 and 'stopped early' in sent[0]
            assert session._turn_result_seen is False  # not an answer
            await session._handle_event(dict(empty))  # once only
        assert len(sent) == 1

    @pytest.mark.parametrize(
        'event',
        [
            {'type': 'result', 'result': '', 'stop_reason': 'end_turn'},
            {'type': 'result', 'result': 'Done.', 'stop_reason': ''},
        ],
    )
    async def test_a_normal_end_is_not_continued(self, event):
        session = _make_session('execute')
        sent: list[str] = []

        async def _noop(*a, **k):
            pass

        async def _send(msg):
            sent.append(msg)

        with (
            patch.object(session, '_emit_message', _noop),
            patch.object(session, 'send_user_message', _send),
        ):
            await session._handle_event(event)
        assert sent == []


class TestSubagentCannotFinishTheTask:
    """Observed in CI: a reviewer subagent called set_task_result with
    its own verdict text as the task's result."""

    async def test_a_subagent_set_task_result_is_refused(self):
        session = _make_session('execute')

        async def _noop(*a, **k):
            pass

        tool = 'mcp__vibe-seller__vibe_seller_set_task_result'
        with patch.object(session, '_emit_message', _noop):
            for parent, tid in (
                (None, 'call_main'),
                ('toolu_spawn', 'call_sub'),
            ):
                await session._handle_event({
                    'type': 'assistant',
                    'parent_tool_use_id': parent,
                    'message': {
                        'content': [
                            {
                                'type': 'tool_use',
                                'id': tid,
                                'name': tool,
                                'input': {'result': 'Status: ok'},
                            }
                        ]
                    },
                })
        decisions = {}
        for tid in ('call_main', 'call_sub'):
            sent = []
            session._send_hook_response = AsyncMock(
                side_effect=lambda rid, out, s=sent: s.append(out)
            )
            await session._handle_hook_callback(
                'req',
                {
                    'callback_id': AUTO_APPROVE_CALLBACK,
                    'input': {
                        'tool_name': tool,
                        'tool_input': {'result': 'Status: ok'},
                        'tool_use_id': tid,
                    },
                },
            )
            decisions[tid] = sent[0]['hookSpecificOutput']['permissionDecision']
        assert decisions == {'call_main': 'allow', 'call_sub': 'deny'}
