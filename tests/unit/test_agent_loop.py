"""Unit tests for agent tool call loop detection.

A loop is an identical call AND an identical answer: legitimate
polling (``sleep 20; browser-use state`` while a report generates)
repeats the call while the answer keeps changing, and must not be
killed; six identical answers to one identical call is the degenerate
loop the breaker is for.
"""

import hashlib

import pytest

from app.ai.claude_backend import AgentSession
import app.ai.claude_backend_utils as _mod
from app.ai.claude_backend_utils import check_tool_loop, record_tool_result

# ``_note_tool_results`` is a _HookMixin method, merged into the public
# AgentSession; the stream calls it there.
_note_tool_results = AgentSession._note_tool_results

pytestmark = pytest.mark.unit

THRESHOLD = 6
CMD = {'command': 'sleep 20; browser-use state'}


@pytest.fixture(autouse=True)
def _pin_threshold(monkeypatch):
    """Pin MAX_REPEAT_TOOL_CALLS so env overrides don't flake."""
    monkeypatch.setattr(_mod, 'MAX_REPEAT_TOOL_CALLS', THRESHOLD)


def call(recent, i, cmd=CMD):
    """Pre-approve the i-th call; returns the kill decision."""
    return check_tool_loop(recent, f'tu-{i}', 'Bash', cmd)


def answer(recent, i, text):
    record_tool_result(recent, f'tu-{i}', text)


class TestCheckToolLoop:
    """check_tool_loop mutates the session's rolling window in place —
    each test uses its own list, mirroring
    ``AgentSession._recent_tool_calls``."""

    def test_no_loop_below_threshold(self):
        """5 identical calls with identical answers → not yet a loop."""
        recent = []
        for i in range(THRESHOLD - 1):
            assert call(recent, i) is False
            answer(recent, i, 'still running')

    def test_loop_at_threshold(self):
        """6 identical calls with one repeated answer → loop."""
        recent = []
        for i in range(THRESHOLD - 1):
            assert call(recent, i) is False
            answer(recent, i, 'still running')
        assert call(recent, THRESHOLD - 1) is True

    def test_polling_with_moving_output_is_not_a_loop(self):
        """The real thing the old breaker killed: the same poll, but
        the report is progressing — the answer changes every round."""
        recent = []
        for i in range(THRESHOLD + 6):
            assert call(recent, i) is False
            answer(recent, i, f'{i}% processed')

    def test_unanswered_repeats_are_not_yet_evidence(self):
        """Parallel identical calls: results of siblings are still in
        flight, so nothing is proved — no kill."""
        recent = []
        for i in range(THRESHOLD + 3):
            assert call(recent, i) is False

    def test_different_call_resets(self):
        """5 identical + 1 different → no loop."""
        recent = []
        for i in range(THRESHOLD - 1):
            call(recent, i)
            answer(recent, i, 'same')
        assert check_tool_loop(recent, 'tu-x', 'Read', {'file': 'x'}) is False

    def test_loop_after_different_calls(self):
        """Different calls then a full answered streak → loop."""
        recent = []
        check_tool_loop(recent, 'tu-r', 'Read', {'file': 'a'})
        answer(recent, 'tu-r', 'text')
        check_tool_loop(recent, 'tu-w', 'Write', {'file': 'b'})
        answer(recent, 'tu-w', 'ok')
        for i in range(THRESHOLD - 1):
            assert call(recent, i) is False
            answer(recent, i, 'still running')
        assert call(recent, THRESHOLD - 1) is True

    def test_different_inputs_no_loop(self):
        """Same tool, different inputs, same answers → no loop."""
        recent = []
        for i in range(12):
            result = check_tool_loop(
                recent, f'tu-{i}', 'Bash', {'command': f'cmd{i}'}
            )
            assert result is False
            answer(recent, i, 'done')

    def test_window_slides(self):
        """After a streak is broken, a full new answered streak is
        needed to trigger again."""
        recent = []
        for i in range(THRESHOLD - 1):
            call(recent, i)
            answer(recent, i, 'same')
        check_tool_loop(recent, 'tu-x', 'Read', {'file': 'x'})  # breaks it
        for i in range(THRESHOLD - 1):
            assert (
                check_tool_loop(recent, f'w-{i}', 'Write', {'f': '0'}) is False
            )
            record_tool_result(recent, f'w-{i}', 'ok')
        assert check_tool_loop(recent, 'w-final', 'Write', {'f': '0'}) is True


class TestRecordToolResult:
    def test_fills_the_matching_pending_entry(self):
        recent = []
        check_tool_loop(recent, 'tu-1', 'Bash', {'command': 'x'})
        record_tool_result(recent, 'tu-1', 'out')
        assert recent[0]['result'] is not None

    def test_unknown_id_is_ignored(self):
        """A result for a call the breaker never saw (subagent, or a
        trimmed entry) proves nothing about this window."""
        recent = []
        check_tool_loop(recent, 'tu-1', 'Bash', {'command': 'x'})
        record_tool_result(recent, 'tu-other', 'out')
        assert recent[0]['result'] is None

    def test_missing_id_is_ignored(self):
        recent = []
        check_tool_loop(recent, None, 'Bash', {'command': 'x'})
        record_tool_result(recent, None, 'out')
        assert recent[0]['result'] is None

    def test_digest_not_bytes(self):
        recent = []
        check_tool_loop(recent, 'tu-1', 'Bash', {'command': 'x'})
        record_tool_result(recent, 'tu-1', 'x' * 100_000)
        assert len(recent[0]['result']) < 200

    def test_second_fill_does_not_overwrite(self):
        recent = []
        check_tool_loop(recent, 'tu-1', 'Bash', {'command': 'x'})
        record_tool_result(recent, 'tu-1', 'first')
        record_tool_result(recent, 'tu-1', 'second')
        assert recent[0]['result'] == hashlib.sha256(b'first').hexdigest()


class _WindowOnly:
    """The minimal self ``_note_tool_results`` needs (the stream calls
    it on the AgentSession, whose only other input is the window)."""

    def __init__(self):
        self._recent_tool_calls = []


class TestStreamFeedsTheBreaker:
    """Results arrive as ``tool_result`` blocks in user messages; the
    session method must land them on the matching window entry."""

    def test_tool_result_blocks_fill_entries(self):
        w = _WindowOnly()
        check_tool_loop(w._recent_tool_calls, 'tu-1', 'Bash', {'command': 'x'})
        _note_tool_results(
            w,
            {
                'message': {
                    'content': [
                        {
                            'type': 'tool_result',
                            'tool_use_id': 'tu-1',
                            'content': [
                                {'type': 'text', 'text': 'still running'}
                            ],
                        }
                    ]
                }
            },
        )
        assert w._recent_tool_calls[0]['result'] is not None

    def test_answered_repeats_kill_through_the_real_path(self):
        """Same call, same answer, six times — the breaker must fire
        with nothing but stream events filling it in."""
        w = _WindowOnly()
        for i in range(THRESHOLD - 1):
            assert call(w._recent_tool_calls, i) is False
            _note_tool_results(
                w,
                {
                    'message': {
                        'content': [
                            {
                                'type': 'tool_result',
                                'tool_use_id': f'tu-{i}',
                                'content': 'still running',
                            }
                        ]
                    }
                },
            )
        assert call(w._recent_tool_calls, THRESHOLD - 1) is True
