"""Unit tests for agent tool call loop detection."""

import pytest

import app.ai.claude_backend as _mod
from app.ai.claude_backend import AgentSession

pytestmark = pytest.mark.unit

THRESHOLD = 6


@pytest.fixture(autouse=True)
def _pin_threshold(monkeypatch):
    """Pin MAX_REPEAT_TOOL_CALLS so env overrides don't flake."""
    monkeypatch.setattr(_mod, 'MAX_REPEAT_TOOL_CALLS', THRESHOLD)


class TestCheckToolLoop:
    def _make_session(self):
        return AgentSession(
            task_id='test-task',
            prompt='test',
            mode='execute',
        )

    def test_no_loop_below_threshold(self):
        """5 identical calls (below 6) → no loop."""
        s = self._make_session()
        for _ in range(THRESHOLD - 1):
            assert s._check_tool_loop('Bash', {'command': ':0'}) is False

    def test_loop_at_threshold(self):
        """6 identical calls → loop detected."""
        s = self._make_session()
        for _ in range(THRESHOLD - 1):
            s._check_tool_loop('Bash', {'command': ':0'})
        assert s._check_tool_loop('Bash', {'command': ':0'}) is True

    def test_different_call_resets(self):
        """5 identical + 1 different → no loop."""
        s = self._make_session()
        for _ in range(THRESHOLD - 1):
            s._check_tool_loop('Bash', {'command': ':0'})
        # Different call breaks the streak
        assert s._check_tool_loop('Read', {'file': 'x'}) is False

    def test_loop_after_different_calls(self):
        """Different calls then 6 identical → loop."""
        s = self._make_session()
        s._check_tool_loop('Read', {'file': 'a'})
        s._check_tool_loop('Write', {'file': 'b'})
        for _ in range(THRESHOLD - 1):
            s._check_tool_loop('Bash', {'command': ':0'})
        assert s._check_tool_loop('Bash', {'command': ':0'}) is True

    def test_different_inputs_no_loop(self):
        """Same tool, different inputs → no loop."""
        s = self._make_session()
        for i in range(10):
            result = s._check_tool_loop('Bash', {'command': f'cmd{i}'})
            assert result is False

    def test_window_slides(self):
        """After a streak is broken by a different call,
        need a full new streak to trigger again."""
        s = self._make_session()
        # Build up streak but break it with a different call
        for _ in range(THRESHOLD - 1):
            s._check_tool_loop('Bash', {'command': ':0'})
        s._check_tool_loop('Read', {'file': 'x'})  # breaks streak
        # Now need a full new streak of Write calls
        for _ in range(THRESHOLD - 1):
            assert s._check_tool_loop('Write', {'f': '0'}) is False
        assert s._check_tool_loop('Write', {'f': '0'}) is True
