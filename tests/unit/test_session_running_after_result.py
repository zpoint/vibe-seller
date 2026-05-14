"""Regression guard: ``AgentSession.running`` must turn False once
stdin is closed, not only when the OS reaps the subprocess.

Pre-fix race (CI run 25490774935, test_compaction step 8): the
agent's ``result`` event closed stdin, but ``running`` still
returned True because the subprocess hadn't exited yet (a small
window — ~100ms in the failing log). A follow-up POST arriving in
that window took the inline-stdin path
(``agent_manager.send_message``) which silently no-op'd — the test
then waited 600s for a result that would never come.

The contract: if ``running`` is True, ``send_user_message`` must
deliver a message the agent will process. Once stdin is closed, the
session can't honor that contract — so ``running`` reflects "input
capable", not "process not yet reaped".
"""

from types import SimpleNamespace

import pytest

from app.ai.claude_backend import AgentSession

pytestmark = pytest.mark.unit


def _make_session():
    s = AgentSession.__new__(AgentSession)
    s._proc = None
    s._input_closed = False
    s._stopping = False
    return s


class TestRunningInputCapable:
    def test_no_proc_is_not_running(self):
        s = _make_session()
        assert s.running is False

    def test_alive_proc_is_running(self):
        s = _make_session()
        s._proc = SimpleNamespace(returncode=None)
        assert s.running is True

    def test_exited_proc_is_not_running(self):
        s = _make_session()
        s._proc = SimpleNamespace(returncode=0)
        assert s.running is False

    def test_input_closed_overrides_alive_proc(self):
        """The race: proc still alive, but stdin already closed."""
        s = _make_session()
        s._proc = SimpleNamespace(returncode=None)
        s._input_closed = True
        assert s.running is False

    def test_stopping_overrides_alive_proc(self):
        """``stop()`` sets ``_stopping`` before stdin is touched."""
        s = _make_session()
        s._proc = SimpleNamespace(returncode=None)
        s._stopping = True
        assert s.running is False
