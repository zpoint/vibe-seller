"""Regression guard: ``AgentSession.running`` must turn False once
stdin is closed, not only when the OS reaps the subprocess.

Under the process-per-turn model stdin stays open past result
events (the quiescence watchdog owns the close), so ``running``
now reports True for the whole live turn — but the invariant these
tests pin is unchanged: once the TURN TERMINATOR fires (watchdog /
legacy result-close / plan-skip / stop()) and ``_input_closed``
flips, ``running`` must be False so a follow-up routes to a fresh
``--resume`` spawn. Delivery is additionally confirmed per-write
(``send_user_message`` returns False on a dead pipe and the router
falls through to the spawn path) — the original race (CI run
25490774935: a follow-up silently no-op'd into a dying pipe, test
waited 600s) is closed at both ends.

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
