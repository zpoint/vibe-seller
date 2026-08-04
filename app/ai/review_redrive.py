"""How much of the clock a review gate may spend before it fails open.

The gate's terminal behaviour was always "fail open with an UNVERIFIED
banner" — the design question is only what reaches it. The first version
counted **attempts**: five re-drives, each a full agent turn. Nothing in
that number refers to time, and a turn is not a fixed cost. Observed at
100-150s/turn, five re-drives alone are 500-750s, which is more than the
whole budget the caller allows a run. So the gate could legitimately
spend every second a task had and the caller would kill a run that was
already holding a finished, valid deliverable — the fail-open path
reached by running out of clock instead of by decision.

This class adds the missing denominator. It starts at the FIRST re-drive
(a task that never trips a gate never starts a clock), and it refuses
the next re-drive when the remaining budget cannot fit one — using the
longest re-drive turn observed **on this session** as the estimate,
because that is the only honest prediction of what the next one costs.
Failing open then is the same designed outcome, just reached
deliberately and with time left for the caller to record it.

**The budget is gate-relative, not run-relative.** The server does not
know what deadline its caller (an e2e poll, a scheduler, a person
watching) is willing to wait — so bounding "the whole run" here would be
inventing a number. Bounding what the GATE may add on top of the work is
something this layer genuinely knows, and it is what was unbounded.

Wall clock via ``time.monotonic`` so a system clock step cannot extend
or collapse a budget; injectable for tests.
"""

from __future__ import annotations

from collections.abc import Callable
import time


class RedriveClock:
    """Wall-clock budget for one session's review-gate re-drives.

    Not thread-safe and does not need to be: every caller runs on the
    session's own event loop.
    """

    def __init__(
        self,
        budget_s: float,
        now: Callable[[], float] = time.monotonic,
    ):
        self._budget_s = float(budget_s)
        self._now = now
        # None until the first re-drive — a session that never trips a
        # gate must never be on a clock.
        self._deadline: float | None = None
        self._turn_started_at: float | None = None
        self._longest_turn_s: float = 0.0
        self._turns: int = 0

    @property
    def enabled(self) -> bool:
        """``0`` (or less) disables the time bound — attempts only."""
        return self._budget_s > 0

    @property
    def started(self) -> bool:
        return self._deadline is not None

    @property
    def longest_turn_s(self) -> float:
        return self._longest_turn_s

    def note_redrive(self) -> None:
        """A re-drive was just sent: start the budget, time the turn."""
        now = self._now()
        if self._deadline is None:
            self._deadline = now + self._budget_s
        self._turn_started_at = now
        self._turns += 1

    def note_turn_end(self) -> None:
        """A re-driven turn came back — record what it cost.

        Idempotent and safe to call when no re-drive is outstanding (a
        gate can be satisfied on the very first result), so the caller
        does not have to track whether it is mid-re-drive.
        """
        if self._turn_started_at is None:
            return
        self._longest_turn_s = max(
            self._longest_turn_s, self._now() - self._turn_started_at
        )
        self._turn_started_at = None

    def remaining_s(self) -> float:
        """Seconds left in the budget; ``inf`` when no clock is running."""
        if not self.enabled or self._deadline is None:
            return float('inf')
        return self._deadline - self._now()

    def out_of_time(self) -> bool:
        """True when another re-drive cannot fit in what is left.

        The first re-drive always runs: ``_longest_turn_s`` is 0 until a
        re-driven turn has completed, so this only bites once the
        session has evidence of what a turn costs.
        """
        if not self.enabled or self._deadline is None:
            return False
        return self.remaining_s() <= self._longest_turn_s

    def describe(self) -> str:
        """One clause for the fail-open log line and the transcript.

        Says which bound actually decided, because "the gate gave up" and
        "the gate ran out of clock" want different follow-ups from
        whoever reads the log next.
        """
        if not self.enabled:
            return f'time budget disabled, {self._turns} re-drives sent'
        if self._deadline is None:
            return 'no re-drive was ever issued, so no clock started'
        return (
            f'{self._turns} re-drives sent, {self.remaining_s():.0f}s left of '
            f'a {self._budget_s:.0f}s gate budget, and the longest re-driven '
            f'turn took {self._longest_turn_s:.0f}s'
        )
