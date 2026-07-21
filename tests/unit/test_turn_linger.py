"""The quiescence watchdog (claude_backend_turns._TurnLifecycleMixin).

A turn is one CLI process and the process never exits on its own with
stdin open (spike-verified) — so closing stdin is the turn terminator,
and these tests pin WHEN it may fire: only on a provably quiescent
turn (accepted result, gates pass, no async subagents, no pending
question, not planning), after the tiered idle window; plus the hard
backstops (total-silence close, post-close kill escalation).
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.claude_backend import AgentSession
from app.env_options import Options

pytestmark = pytest.mark.unit


def _session(mode: str = 'execute') -> AgentSession:
    s = AgentSession(task_id='test-task', prompt='test', mode=mode)
    return s


def _tiers(monkeypatch, linger='60', quiet='5', hard='600'):
    monkeypatch.setenv(Options.TURN_LINGER_S.env_var, linger)
    monkeypatch.setenv(Options.TURN_LINGER_QUIET_S.env_var, quiet)
    monkeypatch.setenv(Options.TURN_HARD_IDLE_S.env_var, hard)


def _idle(s: AgentSession, seconds: float):
    s._last_activity_at = time.monotonic() - seconds


class TestTierSelection:
    def test_quiet_tier_without_async_spawns(self, monkeypatch):
        _tiers(monkeypatch)
        s = _session()
        assert s._turn_linger_seconds() == 5.0

    def test_async_tier_after_async_spawn(self, monkeypatch):
        _tiers(monkeypatch)
        s = _session()
        s._had_async_spawns = True
        assert s._turn_linger_seconds() == 60.0

    def test_defaults_are_active_tiers(self, monkeypatch):
        # Rollout stage 2 (the flip): with no env overrides the linger
        # is ACTIVE — 5s quiet tier, 60s async tier.
        monkeypatch.delenv(Options.TURN_LINGER_S.env_var, raising=False)
        monkeypatch.delenv(Options.TURN_LINGER_QUIET_S.env_var, raising=False)
        s = _session()
        assert s._turn_linger_seconds() == 5.0
        s._had_async_spawns = True
        assert s._turn_linger_seconds() == 60.0


class TestCloseGuards:
    """_turn_close_blocked: the must-NOT-close chain."""

    def _closable(self, s):
        # A session whose turn is genuinely done.
        s._turn_result_seen = True
        return s

    def test_closable_when_quiescent(self, monkeypatch):
        s = self._closable(_session())
        with (
            patch(
                'app.ai.claude_backend_turns.check_review_status_for_stop',
                return_value=None,
            ),
            patch(
                'app.ai.claude_backend_turns.check_exec_review_status_for_stop',
                return_value=None,
            ),
        ):
            assert s._turn_close_blocked() is None

    def test_pending_question_blocks(self):
        s = self._closable(_session())
        s._pending_questions['req'] = {'q': 'which sku?'}
        assert s._turn_close_blocked() == 'ask_user_question_pending'

    def test_planning_phase_blocks(self):
        s = _session('plan_then_execute')
        s._turn_result_seen = True
        assert s._executing is False
        assert s._turn_close_blocked() == 'planning_phase'

    def test_no_accepted_result_blocks(self):
        s = _session()
        assert s._turn_close_blocked() == 'no_accepted_result'

    def test_running_async_subagents_block(self):
        s = self._closable(_session())
        s._async_agents['toolu_1'] = 'agent-1'
        assert s._turn_close_blocked() == 'async_subagents_running'

    def test_unsatisfied_review_gate_blocks(self):
        s = self._closable(_session())
        with patch(
            'app.ai.claude_backend_turns.check_review_status_for_stop',
            return_value='Reviewer never ran.',
        ):
            assert s._turn_close_blocked() == 'review_gate_unsatisfied'

    def test_gate_fails_open_past_redrive_budget(self, monkeypatch):
        # Same fail-open bound as the result branch: past the budget
        # the gate stands down so an unsatisfiable gate can't wedge
        # the process forever.
        s = self._closable(_session())
        s._review_redrive_count = 99
        with patch(
            'app.ai.claude_backend_turns.check_review_status_for_stop',
            return_value='Reviewer never ran.',
        ):
            assert s._turn_close_blocked() is None


class TestWatchdogTick:
    async def test_soft_close_after_idle_window(self, monkeypatch):
        _tiers(monkeypatch)
        s = _session()
        s._turn_result_seen = True
        _idle(s, 10)
        emitted = []

        async def _emit(role, content):
            emitted.append((role, content))

        with (
            patch.object(s, '_emit_message', _emit),
            patch(
                'app.ai.claude_backend_turns.check_review_status_for_stop',
                return_value=None,
            ),
            patch(
                'app.ai.claude_backend_turns.check_exec_review_status_for_stop',
                return_value=None,
            ),
        ):
            await s._maybe_close_idle_turn()

        assert s._input_closed is True
        assert any(
            r == 'agent_event' and 'turn_idle_close' in c for r, c in emitted
        )

    async def test_activity_resets_deadline(self, monkeypatch):
        _tiers(monkeypatch)
        s = _session()
        s._turn_result_seen = True
        _idle(s, 2)  # < 5s quiet tier

        async def _noop(*a, **k):
            pass

        with patch.object(s, '_emit_message', _noop):
            await s._maybe_close_idle_turn()
        assert s._input_closed is False

    async def test_blocked_turn_never_soft_closes(self, monkeypatch):
        _tiers(monkeypatch)
        s = _session()
        s._turn_result_seen = True
        s._async_agents['toolu_1'] = 'agent-1'
        _idle(s, 300)

        async def _noop(*a, **k):
            pass

        with patch.object(s, '_emit_message', _noop):
            await s._maybe_close_idle_turn()
        assert s._input_closed is False

    async def test_hard_idle_overrides_async_hold(self, monkeypatch):
        # Total stream silence for the hard window means the subagents
        # are dead, not slow — a live subagent streams events through
        # the parent. Close despite the async hold.
        _tiers(monkeypatch, hard='600')
        s = _session()
        s._turn_result_seen = True
        s._async_agents['toolu_1'] = 'agent-1'
        _idle(s, 700)

        async def _noop(*a, **k):
            pass

        with patch.object(s, '_emit_message', _noop):
            await s._maybe_close_idle_turn()
        assert s._input_closed is True

    async def test_hard_idle_respects_pending_question(self, monkeypatch):
        _tiers(monkeypatch, hard='600')
        s = _session()
        s._pending_questions['req'] = {'q': 'which sku?'}
        _idle(s, 100000)

        async def _noop(*a, **k):
            pass

        with patch.object(s, '_emit_message', _noop):
            await s._maybe_close_idle_turn()
        assert s._input_closed is False

    async def test_hard_idle_disabled_at_zero(self, monkeypatch):
        _tiers(monkeypatch, linger='0', quiet='0', hard='0')
        s = _session()
        _idle(s, 100000)

        async def _noop(*a, **k):
            pass

        with patch.object(s, '_emit_message', _noop):
            await s._maybe_close_idle_turn()
        assert s._input_closed is False

    async def test_post_close_kill_escalation(self, monkeypatch):
        # Stdin closed but the CLI won't die (the GLM stall-after-
        # result wedge): after the grace period the watchdog escalates
        # signals — the bound the old design never had.
        _tiers(monkeypatch)
        s = _session()
        s._input_closed = True
        s._stdin_closed_at = time.monotonic() - 500
        s._force_kill = AsyncMock()

        await s._maybe_close_idle_turn()

        s._force_kill.assert_awaited_once()

    async def test_post_close_within_grace_no_kill(self, monkeypatch):
        _tiers(monkeypatch)
        s = _session()
        s._input_closed = True
        s._stdin_closed_at = time.monotonic() - 5
        s._force_kill = AsyncMock()

        await s._maybe_close_idle_turn()

        s._force_kill.assert_not_awaited()


class TestSendUserMessageTurnReset:
    async def test_injection_opens_new_turn(self):
        s = _session()
        s._turn_result_seen = True
        # No live proc → delivery fails, but the turn reset is the
        # contract under test (a delivered injection must always reset
        # BEFORE the write races the next result event).
        delivered = await s.send_user_message('do more')
        assert delivered is False
        assert s._turn_result_seen is False

    async def test_stopping_session_refuses(self):
        s = _session()
        s._stopping = True
        assert await s.send_user_message('late') is False
