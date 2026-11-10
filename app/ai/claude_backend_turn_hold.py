"""The turn hold's bound, and the state that says it has been spent.

Mixed into AgentSession (sibling of ``_StreamMixin`` / ``_TurnLifecycleMixin``
/ ``_SubagentMixin``).

**Why this is one module.** Four places need the same answer — "may the
hold still keep this turn open?": the result branch that decides whether
to re-drive, the quiescence watchdog that decides whether to close stdin,
and the two Stop-hook deny chains. Each used to work it out for itself
against ``TURN_HOLD_MAX``, and they drifted: after the result branch
gave up and shipped the open-work banner, the watchdog went on blocking
the close on a pending async subagent — one branch of the very composite
the fail-open had just abandoned. A run that failed open at 08:45:35
holding a valid result did not reach a terminal status until the 600s
hard-idle backstop fired at 08:55:36, by which point its caller was gone.

So the state, the bound, the composite, and the two messages live here,
and the four callers ask rather than decide.

**Two denominators.** ``TURN_HOLD_MAX`` attempts, and a wall clock
(``app/ai/turn_hold_budget.py``). Attempts alone were the original bug: a
re-drive is a full agent turn, five of them at 100-150s is 500-750s, and
that outlasts any deadline a caller holds a run open for — so the hold
could spend a task's entire budget and have the run killed while holding
a finished deliverable.
"""

from __future__ import annotations

import logging

from app.ai.claude_backend_utils import TURN_HOLD_MAX
from app.ai.stop_gates.listing_upload_gate import check_upload_verdicts
from app.ai.turn_hold_budget import ledger_for
from app.env_options import Options

logger = logging.getLogger(__name__)

# What the agent is told when a hold refuses to let the turn end. The
# `set_task_error` warning is load-bearing: an agent that reads "you
# cannot finish" as "this task failed" will mark the WHOLE task FAILED
# over a caveat, throwing away a deliverable that merely had work still
# open.
REDRIVE_INSTRUCTION = (
    'You cannot finish yet — work this turn started is still open. '
    'Do NOT just re-answer; act on this and then finish. If you '
    'genuinely cannot satisfy it, finish normally and state the '
    'remaining caveats IN YOUR RESULT — never call '
    'vibe_seller_set_task_error for caveats or partial work (that '
    'channel marks the whole task FAILED and is only for a task with no '
    'usable deliverable):\n\n'
)


def open_work_banner(reason: str) -> str:
    """Prefix for a result that ended with its open work unresolved."""
    first = (reason or '').strip().splitlines()[0] if reason else ''
    return '> ⚠️ **Ended with open work.** ' + first[:300] + '\n\n'


class _TurnHoldMixin:
    """Owns the re-drive budget and the fail-open decision."""

    def _init_turn_hold_state(self):
        """Hold state for this session, on the TURN's budget.

        ``_hold_clock`` — attempts AND the wall clock they
        cost, both read off a ledger keyed by task rather than built
        fresh here. A session is not the unit being bounded: the hold's
        own re-drives each spawn one, and a circuit-breaker kill is
        picked back up in another. Building fresh state per session
        refunded the budget on exactly the respawn the hold provoked, so
        the hold was bounded at every point and the turn was bounded
        nowhere — see ``turn_hold_budget._ledgers``. ``reset_ledger`` at
        the orchestrator entry points is what makes a NEW turn start
        over.

        ``_hold_failed_open`` — the hold has given up and the
        banner has shipped; from then on nothing it was waiting for may
        keep the turn alive. Stays per-session: it describes what THIS
        session already shipped.
        """
        self._hold_clock = ledger_for(
            self.task_id, Options.TURN_HOLD_BUDGET_S.get_float()
        )
        self._hold_failed_open: bool = False
        # The upload verdict holds a turn open once, like every check:
        # after it has been raised, an unverified batch ships as a caveat.
        self._upload_hold_spent: bool = False

    @property
    def _hold_redrive_count(self) -> int:
        """Attempts spent on this turn — the ledger is the only tally."""
        return self._hold_clock.turns

    def _open_work_reason(self) -> str | None:
        """Why this turn may not end yet: a listing batch it uploaded to
        the live account has no clean verdict (``listing_upload_gate``).

        Deliberately does NOT include pending async subagents: the result
        branch adds that (a turn cannot end under live work), while the
        watchdog tracks it separately as its own close-blocker.
        """
        if self.task_dir is None or self._upload_hold_spent:
            return None
        return check_upload_verdicts(self.task_dir)

    def _hold_exhausted(self) -> bool:
        """Has the hold spent everything it is allowed to spend?

        Exhausted on EITHER denominator — the attempt cap, or a time
        budget with no room left for another turn.
        """
        if self._hold_redrive_count >= TURN_HOLD_MAX:
            return True
        return self._hold_clock.out_of_time()

    def _note_hold_turn_end(self) -> None:
        """A re-driven turn came back — bank what it cost.

        Called before asking whether another one fits, because this
        result IS the evidence the estimate is built from. A no-op when
        no re-drive is outstanding.
        """
        self._hold_clock.note_turn_end()

    def _note_hold_redrive(self) -> None:
        """Record (and log) that another re-drive is being issued.

        One increment, on the ledger — the attempt tally reads back off
        it. Keeping a second counter beside the clock is what let the
        two denominators disagree about what a re-drive was.
        """
        self._hold_clock.note_redrive()
        logger.warning(
            'Open work at result for %s — holding the turn and re-driving '
            'agent (%d/%d, %s) instead of closing the control channel',
            self.task_id[:8],
            self._hold_redrive_count,
            TURN_HOLD_MAX,
            self._hold_clock.describe(),
        )

    def _note_hold_failed_open(self) -> None:
        """Give up: the result ships banner-marked, the turn may end.

        Never a tool-denial limbo — the Stop hooks stand down (see the
        ``_deny_stop_if_*`` chain in ``claude_backend_subagents``) and the
        watchdog stops blocking on what this abandoned. Both follow from
        the flag, which is what makes the decision terminal rather than
        merely logged.
        """
        self._hold_failed_open = True
        logger.warning(
            'Open work still unresolved for %s (%d/%d re-drives, %s) '
            '— failing open with the open-work banner',
            self.task_id[:8],
            self._hold_redrive_count,
            TURN_HOLD_MAX,
            self._hold_clock.describe(),
        )

    def _hold_budget_note(self) -> str:
        """Which bound decided, for the transcript event."""
        return self._hold_clock.describe()
