"""Turn lifecycle for AgentSession — the quiescence watchdog.

Mixed into AgentSession via multiple inheritance (sibling of
``_HookMixin`` / ``_StreamMixin`` / ``_SubagentMixin``).

The model (vibe-kanban style): **a turn is one CLI process, and the
turn ends when the process exits** — the CLI is the only component
that knows when its subagents and its task-notification loop are
finished. Empirically (spike vs claude 2.1.215, stream-json in/out,
stdin held open) the CLI never exits on its own while stdin is open:
after the final result it simply waits for the next user message. So
an explicit stdin close is ALWAYS the turn terminator; the design
question is only *when*. Closing at the first result event (the old
design) killed the approval channel under still-running async
subagents and amputated the notification loop. This module closes
stdin only when the turn is provably quiescent:

- an accepted result exists for the current turn, AND
- the review/exec gates pass (same composite the result branch uses),
  AND
- no tracked async subagents are pending, AND
- no stream/stdin activity for the linger window (tiered: longer when
  async subagents were launched this process — late notifications and
  NESTED subagent spawns are invisible to tracking and need the
  grace), AND
- never while an AskUserQuestion is pending (operator can take hours)
  or a plan approval is parked (planning phase).

Backstops, both strictly safer than the old design:

- HARD idle: no stream event at all for ``VIBE_TURN_HARD_IDLE_S`` →
  close regardless of gate/async state (true silence that long means
  the work is dead, not slow — a live subagent streams events through
  the parent).
- Post-close kill escalation: some providers (GLM-4.7, documented in
  ``_persist_session_id``) stall between their final result and
  process exit even after stdin closes. The old design left that
  wedge unbounded (the readline heartbeat keeps ``Task.updated_at``
  fresh, defeating the stall reaper, and ``_wait_for_session_end``
  has no time backstop). Now: once stdin is closed, if the process is
  still alive after a grace period, ``_force_kill()`` escalates and
  the heartbeat stops.

All windows come from env options (``VIBE_TURN_LINGER_S`` /
``VIBE_TURN_LINGER_QUIET_S`` / ``VIBE_TURN_HARD_IDLE_S``); ``0``
means "close at the result event" (exact legacy behavior) and is the
rollout default until the flip PR.
"""

import json
import logging
import time

from app.ai.claude_backend_utils import (
    REVIEW_REDRIVE_MAX,
    check_exec_review_status_for_stop,
    check_review_status_for_stop,
)
from app.env_options import Options

logger = logging.getLogger(__name__)

# After a linger/hard close, how long the process may take to exit
# before the signal escalation fires (seconds). Two heartbeat ticks.
_POST_CLOSE_KILL_GRACE_S = 120.0


class _TurnLifecycleMixin:
    """Quiescence watchdog: when to end the CLI process."""

    def _init_turn_state(self):
        """Per-session turn-lifecycle state.

        ``_turn_result_seen`` — an accepted execute-phase result exists
        for the CURRENT turn; reset by every user-message injection
        (follow-up or gate redrive) so each injected turn earns its own
        result. ``_last_result_is_error`` — the LAST accepted result's
        is_error (assigned, never sticky; folded into
        ``_is_error_result`` at process exit so a recovered turn ends
        COMPLETED while forced errors — circuit breaker, rc!=0 — stay
        FAILED). ``_had_async_spawns`` — an async subagent launch ack
        was seen this process (selects the linger tier).
        ``_last_activity_at`` — monotonic time of the last stream event
        or stdin write. ``_stdin_closed_at`` — when the watchdog closed
        stdin (arms the post-close kill escalation).
        """
        self._turn_result_seen: bool = False
        self._last_result_is_error: bool = False
        self._had_async_spawns: bool = False
        self._last_activity_at: float = time.monotonic()
        self._stdin_closed_at: float | None = None

    def _turn_linger_seconds(self) -> float:
        """The soft-linger window for this process's current state."""
        if self._had_async_spawns:
            return Options.TURN_LINGER_S.get_float()
        return Options.TURN_LINGER_QUIET_S.get_float()

    def _turn_close_blocked(self) -> str | None:
        """Reason the soft close must NOT happen yet; None = closable.

        Ordered cheapest-first. The gate composite mirrors the result
        branch exactly (including the fail-open past the redrive
        budget) so "accepted result" and "closable" can never disagree
        about gate state.
        """
        if self._pending_questions:
            return 'ask_user_question_pending'
        if self.mode == 'plan_then_execute' and not self._executing:
            return 'planning_phase'
        if not self._turn_result_seen:
            return 'no_accepted_result'
        if self._async_agents:
            return 'async_subagents_running'
        if self._review_redrive_count < REVIEW_REDRIVE_MAX:
            gate = check_review_status_for_stop(
                self.task_dir,
                subagent_ran=getattr(self, '_review_subagent_ran', False),
                review_writers=getattr(self, '_review_file_writers', None),
            ) or check_exec_review_status_for_stop(
                self.task_dir,
                review_writers=getattr(self, '_review_file_writers', None),
            )
            if gate:
                return 'review_gate_unsatisfied'
        return None

    async def _maybe_close_idle_turn(self):
        """Watchdog tick — called from the stream readline-timeout branch.

        Evaluates, in order: post-close kill escalation, hard idle,
        soft linger. Quiet no-op while the stream is active (the
        caller only reaches here when readline timed out, i.e. the
        stream has been silent for at least one heartbeat interval).
        """
        now = time.monotonic()
        # 1. Post-close escalation: stdin closed but the CLI won't die
        #    (the GLM stall-after-result wedge). Bounded here because
        #    nothing else bounds it — the heartbeat keeps the stall
        #    reaper away and the session waiter has no timeout.
        if self._stdin_closed_at is not None:
            if now - self._stdin_closed_at > _POST_CLOSE_KILL_GRACE_S:
                logger.warning(
                    'Process for %s still alive %.0fs after stdin close '
                    '— escalating signals',
                    self.task_id[:8],
                    now - self._stdin_closed_at,
                )
                await self._force_kill()
            return
        idle = now - self._last_activity_at
        # 2. Hard idle: total stream silence overrides gate/async holds
        #    (a live subagent streams events through the parent; this
        #    long with nothing at all means the work is dead). Pending
        #    AskUserQuestion still blocks — that idle is legitimate.
        hard = Options.TURN_HARD_IDLE_S.get_float()
        if (
            hard > 0
            and idle >= hard
            and not self._pending_questions
            and not (self.mode == 'plan_then_execute' and not self._executing)
        ):
            await self._close_stdin(f'hard_idle_{int(idle)}s')
            return
        # 3. Soft linger: the turn is done and the process has been
        #    quiet for the tier window — end it.
        linger = self._turn_linger_seconds()
        if linger <= 0:
            return  # legacy mode closes inline at the result event
        if idle < linger:
            return
        blocked = self._turn_close_blocked()
        if blocked:
            logger.debug(
                'Linger close for %s blocked: %s', self.task_id[:8], blocked
            )
            return
        await self._close_stdin(f'quiescent_{int(idle)}s')

    async def _close_stdin(self, reason: str, emit: bool = True):
        """Close the CLI's stdin — the turn terminator. Idempotent.

        Emits a ``turn_idle_close`` agent_event so transcripts show WHY
        a turn ended (post-mortems on early/late closes need this).
        ``emit=False`` for the legacy inline close (linger=0 at the
        result event) — that path predates the event and stays silent
        so rollout stage 1 is byte-identical in message streams.
        """
        if self._input_closed:
            return
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
        self._input_closed = True
        if self._stdin_closed_at is None:
            self._stdin_closed_at = time.monotonic()
        if emit:
            await self._emit_message(
                'agent_event',
                json.dumps({'event': 'turn_idle_close', 'reason': reason}),
            )
