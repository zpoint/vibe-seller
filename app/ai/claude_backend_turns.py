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

- an accepted result exists for the current turn (the review/exec
  gates have passed), AND
- no tracked async work (subagents OR background shell commands) is
  pending — the hold waits for this and nothing else — AND
- no stream/stdin activity for the linger window (tiered: longer when
  async subagents were launched this process — late notifications and
  NESTED subagent spawns are invisible to tracking and need the
  grace), AND
- never while an AskUserQuestion is pending (operator can take hours)
  or a plan approval is parked (planning phase).

Backstops, both strictly safer than the old design:

- HARD idle: no stream event at all for ``VIBE_TURN_HARD_IDLE_S`` →
  close regardless of hold/async state (true silence that long means
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

import asyncio
import json
import logging
import time

from app.ai.claude_backend_turn_hold import _TurnHoldMixin
from app.env_options import Options

logger = logging.getLogger(__name__)

# After a linger/hard close, how long the process may take to exit
# before the signal escalation fires (seconds). Two heartbeat ticks.
_POST_CLOSE_KILL_GRACE_S = 120.0


class _TurnLifecycleMixin(_TurnHoldMixin):
    """Quiescence watchdog: when to end the CLI process."""

    def _init_turn_state(self):
        """Per-session turn-lifecycle state.

        ``_turn_result_seen`` — an accepted execute-phase result exists
        for the CURRENT turn; reset by every user-message injection
        (follow-up or hold redrive) so each injected turn earns its own
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
        self._init_turn_hold_state()
        self._turn_result_seen: bool = False
        self._last_result_is_error: bool = False
        self._escalation_reaped: bool = False
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

        Ordered cheapest-first. The hold mirrors the result
        branch exactly — same exhaustion predicate
        (``_hold_exhausted``), and the same standing-down once
        that branch has failed open — so "accepted result" and
        "closable" can never disagree about hold state. They did once,
        and the disagreement cost ten minutes per run.
        """
        if self._pending_questions:
            return 'ask_user_question_pending'
        if self.mode == 'plan_then_execute' and not self._executing:
            return 'planning_phase'
        if not self._turn_result_seen:
            return 'no_accepted_result'
        # The hold has already given up and shipped the banner. What is
        # checked below is exactly what fail-open stopped waiting for —
        # the async subagents — so continuing to block on them keeps a
        # finished, banner-marked deliverable non-terminal until the
        # 600s hard-idle backstop. That gap is what killed a run
        # holding a valid result (see the fail-open branch in
        # claude_backend_stream).
        if self._hold_failed_open:
            return None
        if self._async_agents:
            return 'async_work_running'  # subagents and/or bg shells
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
                self._escalation_reaped = True
                await self._force_kill()
            return
        idle = now - self._last_activity_at
        # 2. Hard idle: total stream silence overrides hold/async holds
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

    async def _wait_process_exit_after_close(self) -> int:
        """Wait for the process to exit — BOUNDED by the same contract
        the watchdog enforces: stdin closed + grace elapsed + still
        alive = force kill.

        The watchdog's post-close escalation only runs while the stream
        loop is still ticking. Once stdout hits EOF the loop is gone, so
        an unbounded ``_proc.wait()`` there was the one place the
        contract was not enforced: a CLI that stalls after its final
        result (the documented GLM wedge) held the task RUNNING with no
        rc, no terminal status, forever (CI: 030d928e sat in silence
        6.5 min until the step was killed, while the identical
        2c6ff324 — whose CLI exited — completed in 8s). The fix is the
        design, not a bigger timeout: the SAME grace the watchdog uses,
        then the SAME escalation.
        """
        proc = self._proc
        if proc is None:
            return -1
        try:
            return await asyncio.wait_for(
                proc.wait(), timeout=_POST_CLOSE_KILL_GRACE_S
            )
        except TimeoutError:
            logger.warning(
                'Process for %s still alive %.0fs after stdin close '
                '— escalating signals',
                self.task_id[:8],
                _POST_CLOSE_KILL_GRACE_S,
            )
            self._escalation_reaped = True
            await self._force_kill()
            return await proc.wait()

    def _reap_counts_as_error(self, return_code: int) -> bool:
        """Did this exit earn the task an error?

        The result branch's invariant — "the LAST accepted result's
        error state is the turn's" — extends to the process: a signal
        WE sent to reap a CLI that stalled after a clean final result
        and our own stdin close is lifecycle cleanup, not a task error
        (Brief #15b: 030d928e delivered its result and must end
        COMPLETED, not FAILED with rc=-9). A reap that found NO
        accepted result — or an error as the last one — still fails the
        task, and so does any non-zero rc the process produced on its
        own.
        """
        if return_code == 0:
            return False
        return not (
            self._escalation_reaped
            and self._turn_result_seen
            and not self._last_result_is_error
        )

    async def _apply_exit_code(self, return_code: int) -> None:
        """Fold the process's exit into the turn's error state.

        ONE rule for every exit path — the task stream loop and the
        workspace assistant's loop both call this, so a reap can never
        be an error in one and cleanup in the other.
        """
        if return_code == 0:
            return
        if self._reap_counts_as_error(return_code):
            self._is_error_result = True
            await self._emit_message(
                'system',
                f'Agent exited with code {return_code}',
            )
        else:
            await self._emit_message(
                'system',
                f'Agent process reaped (rc={return_code}) after its '
                'final result — post-close cleanup, not a task error',
            )

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
