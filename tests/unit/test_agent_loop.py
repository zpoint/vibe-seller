"""Unit tests for agent tool call loop detection."""

import pytest

import app.ai.claude_backend_utils as _mod
from app.ai.claude_backend_utils import check_tool_loop

pytestmark = pytest.mark.unit

THRESHOLD = 6


@pytest.fixture(autouse=True)
def _pin_threshold(monkeypatch):
    """Pin MAX_REPEAT_TOOL_CALLS so env overrides don't flake."""
    monkeypatch.setattr(_mod, 'MAX_REPEAT_TOOL_CALLS', THRESHOLD)


class TestCheckToolLoop:
    """check_tool_loop mutates the session's rolling window in place —
    each test uses its own list, mirroring
    ``AgentSession._recent_tool_calls``."""

    def test_no_loop_below_threshold(self):
        """5 identical calls (below 6) → no loop."""
        recent = []
        for _ in range(THRESHOLD - 1):
            assert check_tool_loop(recent, 'Bash', {'command': ':0'}) is False

    def test_loop_at_threshold(self):
        """6 identical calls → loop detected."""
        recent = []
        for _ in range(THRESHOLD - 1):
            check_tool_loop(recent, 'Bash', {'command': ':0'})
        assert check_tool_loop(recent, 'Bash', {'command': ':0'}) is True

    def test_different_call_resets(self):
        """5 identical + 1 different → no loop."""
        recent = []
        for _ in range(THRESHOLD - 1):
            check_tool_loop(recent, 'Bash', {'command': ':0'})
        # Different call breaks the streak
        assert check_tool_loop(recent, 'Read', {'file': 'x'}) is False

    def test_loop_after_different_calls(self):
        """Different calls then 6 identical → loop."""
        recent = []
        check_tool_loop(recent, 'Read', {'file': 'a'})
        check_tool_loop(recent, 'Write', {'file': 'b'})
        for _ in range(THRESHOLD - 1):
            check_tool_loop(recent, 'Bash', {'command': ':0'})
        assert check_tool_loop(recent, 'Bash', {'command': ':0'}) is True

    def test_different_inputs_no_loop(self):
        """Same tool, different inputs → no loop."""
        recent = []
        for i in range(10):
            result = check_tool_loop(recent, 'Bash', {'command': f'cmd{i}'})
            assert result is False

    def test_window_slides(self):
        """After a streak is broken by a different call,
        need a full new streak to trigger again."""
        recent = []
        # Build up streak but break it with a different call
        for _ in range(THRESHOLD - 1):
            check_tool_loop(recent, 'Bash', {'command': ':0'})
        check_tool_loop(recent, 'Read', {'file': 'x'})  # breaks streak
        # Now need a full new streak of Write calls
        for _ in range(THRESHOLD - 1):
            assert check_tool_loop(recent, 'Write', {'f': '0'}) is False
        assert check_tool_loop(recent, 'Write', {'f': '0'}) is True
