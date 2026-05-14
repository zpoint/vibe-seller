"""Unit tests for result event deduplication in AgentSession.

Verifies that only the first execution-phase result event is emitted
as role='result'; subsequent results are demoted to 'assistant'.
"""

from unittest.mock import patch

import pytest

from app.ai.claude_backend import AgentSession

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

    async def test_empty_result_ignored(self):
        """Empty result text is skipped entirely."""
        session = _make_session('execute')
        emitted: list[tuple[str, str]] = []

        async def _mock_emit(role, content):
            emitted.append((role, content))

        with patch.object(session, '_emit_message', _mock_emit):
            await session._handle_event({'type': 'result', 'result': ''})

        assert len(emitted) == 0
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
