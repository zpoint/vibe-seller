"""Unit tests for result event deduplication in AgentSession.

Verifies that only the first execution-phase result event is emitted
as role='result'; subsequent results are demoted to 'assistant'.
"""

from unittest.mock import Mock, patch

import pytest

from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_stream import REVIEW_REDRIVE_MAX

pytestmark = pytest.mark.unit


def _make_session(mode: str = 'execute') -> AgentSession:
    return AgentSession(
        task_id='test-task',
        prompt='test',
        mode=mode,
    )


class TestHandleEventResultDedup:
    """_handle_event should deduplicate result events."""

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
        assert session._first_result_emitted is True

    async def test_subsequent_results_demoted_to_assistant(self):
        """Second and third result events → role='assistant'."""
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

        assert len(emitted) == 3
        assert emitted[0] == ('result', 'First big result')
        assert emitted[1] == ('assistant', 'Ack 1')
        assert emitted[2] == ('assistant', 'Ack 2')
        # _result_text stays as the first result
        assert session._result_text == 'First big result'

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
        assert session._first_result_emitted is False

    async def test_first_execution_result_after_planning(self):
        """After planning phase, first execution result gets the card."""
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
            # Execution result
            await session._handle_event({
                'type': 'result',
                'result': 'Execution done',
            })
            # Extra execution result
            await session._handle_event({
                'type': 'result',
                'result': 'Background ack',
            })

        assert emitted[0] == ('assistant', 'Plan done')
        assert emitted[1] == ('result', 'Execution done')
        assert emitted[2] == ('assistant', 'Background ack')
        assert session._result_text == 'Execution done'

    async def test_error_result_flag_preserved(self):
        """is_error flag is set regardless of dedup logic."""
        session = _make_session('execute')
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            await session._handle_event({
                'type': 'result',
                'result': 'Error occurred',
                'is_error': True,
            })

        assert session._is_error_result is True
        assert emitted[0] == ('result', 'Error occurred')

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
        assert session._first_result_emitted is True

    async def test_empty_subsequent_result_dropped(self):
        """After the first result, empty results are dropped.

        The end-of-turn signal already fired with the first result;
        a trailing empty result carries no information.
        """
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
        assert session._first_result_emitted is True

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
        assert session._first_result_emitted is False

    async def test_auto_mode_first_result(self):
        """Auto mode behaves same as execute — first result wins."""
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
        assert emitted[1] == ('assistant', 'Extra ack')
        assert session._result_text == 'Auto result'


class TestReviewGateRedrive:
    """A result while a review gate is unsatisfied must NOT end the turn:
    keep the control channel open and re-drive the agent instead of
    closing stdin (which would silently deny the reviewer subagent's
    tool calls). See app/ai/claude_backend_stream result branch."""

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
                'app.ai.claude_backend_stream.check_review_status_for_stop',
                return_value='Reviewer never ran. Spawn the DoD reviewer.',
            ),
            patch(
                'app.ai.claude_backend_stream'
                '.check_exec_review_status_for_stop',
                return_value=None,
            ),
        ):
            await session._handle_event({
                'type': 'result',
                'result': 'placeholder — reviewer running in background',
            })

        # The placeholder result was NOT finalized as the turn's result.
        assert session._first_result_emitted is False
        assert session._result_text == ''
        # The agent was re-driven with the gate's deny reason.
        assert len(sent) == 1
        assert 'Reviewer never ran' in sent[0]
        assert session._review_redrive_count == 1
        # A structured re-drive marker was emitted (not the result card).
        assert any(r == 'agent_event' for r, _ in emitted)
        assert not any(r == 'result' for r, _ in emitted)

    async def test_redrive_is_bounded(self):
        """After REVIEW_REDRIVE_MAX re-drives, the turn is allowed to end
        so an unsatisfiable gate can't wedge the session forever."""
        session = _make_session('auto')
        session.task_dir = '/tmp/fake-task'
        session._review_redrive_count = REVIEW_REDRIVE_MAX
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with (
            patch.object(session, '_emit_message', _mock_emit),
            patch(
                'app.ai.claude_backend_stream.check_review_status_for_stop',
                return_value='still gaps',
            ),
            patch(
                'app.ai.claude_backend_stream'
                '.check_exec_review_status_for_stop',
                return_value=None,
            ),
        ):
            await session._handle_event({
                'type': 'result',
                'result': 'Final answer',
            })

        # Bound hit → the turn still ends (no wedge), but the result is
        # banner-marked UNVERIFIED so an unreviewed deliverable is never
        # mistaken for a reviewed one.
        assert len(emitted) == 1
        role, content = emitted[0]
        assert role == 'result'
        assert content.endswith('Final answer')
        assert 'Unverified result' in content
        assert session._first_result_emitted is True

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
                'app.ai.claude_backend_stream.check_review_status_for_stop',
                return_value=None,
            ),
            patch(
                'app.ai.claude_backend_stream'
                '.check_exec_review_status_for_stop',
                return_value=None,
            ),
        ):
            await session._handle_event({
                'type': 'result',
                'result': 'Verified answer',
            })

        assert emitted == [('result', 'Verified answer')]
        assert session._first_result_emitted is True
        assert session._review_redrive_count == 0

    # ── The core regression: the control channel must stay OPEN while a
    # review gate is unsatisfied. This is the exact invariant the
    # production bug violated — a `result` closed stdin, so the DoD
    # reviewer subagent's tool calls (and the in-place fixes) lost their
    # approval channel and the CLI default-denied all of them. Neither
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
                'app.ai.claude_backend_stream.check_review_status_for_stop',
                return_value='Reviewer never ran — spawn the DoD reviewer.',
            ),
            patch(
                'app.ai.claude_backend_stream'
                '.check_exec_review_status_for_stop',
                return_value=None,
            ),
        ):
            await session._handle_event({
                'type': 'result',
                'result': 'placeholder',
            })

        # The bug: these would be True / called. The fix keeps them clean.
        assert session._input_closed is False
        session._proc.stdin.close.assert_not_called()

    async def test_satisfied_gate_closes_control_channel(self):
        """Gate satisfied → stdin DOES close so the CLI exits (the fix
        must not regress normal end-of-turn)."""
        session = _make_session('auto')
        session.task_dir = '/tmp/fake-task'
        session._proc = Mock()
        session._proc.stdin = Mock()

        async def _noop(*a, **k):
            pass

        with (
            patch.object(session, '_emit_message', _noop),
            patch(
                'app.ai.claude_backend_stream.check_review_status_for_stop',
                return_value=None,
            ),
            patch(
                'app.ai.claude_backend_stream'
                '.check_exec_review_status_for_stop',
                return_value=None,
            ),
        ):
            await session._handle_event({
                'type': 'result',
                'result': 'Verified answer',
            })

        assert session._input_closed is True
        session._proc.stdin.close.assert_called_once()


class TestHandleEventReflectionGuard:
    """_handle_event must distinguish ``_pre_reflection_result``:

    - ``None`` → Stop-hook reflection never fired this turn; use the
      result event's own text as-is.
    - non-empty string → reflection fired AFTER the agent produced
      real exec-phase text; the pre-reflection snapshot is the
      user-facing answer and overrides the result event (which
      otherwise reports reflection content).
    - empty string ``''`` → reflection fired but the agent had no
      exec-phase text (e.g. answer in a ``thinking`` block only);
      the result event's text is reflection content and must be
      suppressed so reflection never becomes the user-facing answer.

    The ``is not None`` check is the contract; this pin guards it.
    """

    async def test_none_pre_reflection_uses_result_text(self):
        """Default ``_pre_reflection_result`` is None → result event
        text passes through unchanged."""
        session = _make_session('execute')
        assert session._pre_reflection_result is None
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

    async def test_nonempty_pre_reflection_overrides_result(self):
        """Pre-reflection snapshot (real answer) overrides the
        post-reflection text in the result event."""
        session = _make_session('execute')
        session._pre_reflection_result = 'The real answer'
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            await session._handle_event({
                'type': 'result',
                'result': 'No transferable learning…',
            })

        assert emitted == [('result', 'The real answer')]
        assert session._result_text == 'The real answer'
        # Always cleared so a later turn does not adopt stale state.
        assert session._pre_reflection_result is None

    async def test_empty_pre_reflection_suppresses_reflection_text(self):
        """Empty-string ``_pre_reflection_result`` proves reflection
        fired but the agent had no exec-phase text. The result
        event's reflection content MUST NOT become the user-facing
        answer — text is overridden to empty. BUT we still emit a
        result message with that empty text: the result event's
        existence is the "turn ended" signal downstream consumers
        (UI, e2e test polls) depend on, so dropping it entirely
        would just trade one bug (wrong text) for another (no
        end-of-turn signal)."""
        session = _make_session('execute')
        session._pre_reflection_result = ''
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            await session._handle_event({
                'type': 'result',
                'result': 'No transferable learning from this task.',
            })

        # Result emitted with empty text — turn-end signal goes out,
        # reflection content does NOT become the answer.
        assert emitted == [('result', '')]
        assert session._first_result_emitted is True
        assert session._result_text == ''
        # Always cleared so a later turn does not adopt stale state.
        assert session._pre_reflection_result is None


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
                'app.ai.claude_backend_stream.check_review_status_for_stop',
                return_value=None,
            ),
            patch(
                'app.ai.claude_backend_stream'
                '.check_exec_review_status_for_stop',
                return_value=None,
            ),
        ):
            for ev in self._spawn_async_agent(session):
                await session._handle_event(ev)
            await session._handle_event({
                'type': 'result',
                'result': 'reviewer is running in the background',
            })

        assert session._first_result_emitted is False
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
                'app.ai.claude_backend_stream.check_review_status_for_stop',
                return_value=None,
            ),
            patch(
                'app.ai.claude_backend_stream'
                '.check_exec_review_status_for_stop',
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
        assert session._first_result_emitted is True

    async def test_task_notification_releases_turn(self):
        session = _make_session('auto')
        session.task_dir = '/tmp/fake-task'

        async def _noop(*a, **k):
            pass

        with (
            patch.object(session, '_emit_message', _noop),
            patch(
                'app.ai.claude_backend_stream.check_review_status_for_stop',
                return_value=None,
            ),
            patch(
                'app.ai.claude_backend_stream'
                '.check_exec_review_status_for_stop',
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

        assert session._first_result_emitted is True

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


class TestReviewFileAuthorshipTracking:
    """The stream attributes each review-file Write/Edit to 'subagent'
    or 'main' via the event's parent_tool_use_id — the signal the gates
    use to reject a self-written accepting verdict."""

    def _write_event(self, parent, path='REVIEW_2026-07-20_iter1.md'):
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

    async def test_main_write_recorded_as_main(self):
        session = _make_session('auto')

        async def _noop(*a, **k):
            pass

        with patch.object(session, '_emit_message', _noop):
            await session._handle_event(self._write_event(None))
        assert session._review_file_writers == {
            'REVIEW_2026-07-20_iter1.md': 'main'
        }

    async def test_subagent_write_recorded_as_subagent(self):
        session = _make_session('auto')

        async def _noop(*a, **k):
            pass

        with patch.object(session, '_emit_message', _noop):
            await session._handle_event(self._write_event('toolu_parent9'))
        assert session._review_file_writers == {
            'REVIEW_2026-07-20_iter1.md': 'subagent'
        }

    async def test_main_overwrite_after_subagent_downgrades(self):
        # Last writer wins: a main-agent rewrite of the reviewer's file
        # must not keep the 'subagent' attribution.
        session = _make_session('auto')

        async def _noop(*a, **k):
            pass

        with patch.object(session, '_emit_message', _noop):
            await session._handle_event(self._write_event('toolu_parent9'))
            await session._handle_event(self._write_event(None))
        assert session._review_file_writers == {
            'REVIEW_2026-07-20_iter1.md': 'main'
        }

    async def test_non_review_files_not_tracked(self):
        session = _make_session('auto')

        async def _noop(*a, **k):
            pass

        with patch.object(session, '_emit_message', _noop):
            await session._handle_event(
                self._write_event(None, path='PREVIEW.md')
            )
            await session._handle_event(
                self._write_event(None, path='notes.txt')
            )
        assert session._review_file_writers == {}

    async def test_exec_review_tracked_too(self):
        session = _make_session('auto')

        async def _noop(*a, **k):
            pass

        with patch.object(session, '_emit_message', _noop):
            await session._handle_event(
                self._write_event(None, path='EXEC_REVIEW_2026-07-20_iter1.md')
            )
        assert session._review_file_writers == {
            'EXEC_REVIEW_2026-07-20_iter1.md': 'main'
        }
