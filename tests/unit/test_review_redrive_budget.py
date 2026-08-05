"""The review gate may not spend the caller's whole clock.

The bug these pin: the re-drive budget was counted in ATTEMPTS. Five
attempts sounds bounded until you price them — a re-drive is a full agent
turn, one was measured at 150s, and five of those outlast any deadline a
caller holds a run open for. A task died holding a finished 530-char
result because the gate had been allowed to spend the entire budget
before the work was judged.

Fail-open with the UNVERIFIED banner was always the designed terminal
behaviour. These tests are about REACHING it deliberately, with time left
over, instead of by running out of clock.
"""

import pytest

from app.ai.claude_backend_review_gate import (
    _ReviewGateMixin,  # noqa: PLC2701 — the unit under test
)
from app.ai.claude_backend_utils import REVIEW_REDRIVE_MAX
from app.ai.review_redrive import (
    RedriveClock,
    _ledgers,  # noqa: PLC2701
    ledger_for,
    redrive_count,
    reset_ledger,
)

pytestmark = pytest.mark.unit


class _Clock:
    """Hand-cranked monotonic clock — no sleeping in a unit test."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float):
        self.t += seconds


def _budget(seconds=300.0):
    clock = _Clock()
    return RedriveClock(seconds, now=clock), clock


class TestTheClockStartsOnlyWhenAGateBites:
    def test_a_session_that_never_redrives_is_never_on_a_clock(self):
        b, clock = _budget()
        clock.advance(10_000)
        assert not b.out_of_time()
        assert b.remaining_s() == float('inf')

    def test_note_turn_end_without_a_redrive_is_a_no_op(self):
        # The caller banks every result, not just re-driven ones — it
        # cannot know in advance which it is holding.
        b, clock = _budget()
        clock.advance(500)
        b.note_turn_end()
        assert b.longest_turn_s == 0.0
        assert not b.out_of_time()


class TestAnotherTurnMustFitInWhatIsLeft:
    def test_the_first_redrive_always_runs(self):
        # Nothing is known about turn cost yet, so refusing here would
        # be refusing on a guess.
        b, _ = _budget()
        b.note_redrive()
        assert not b.out_of_time()

    def test_a_turn_that_would_overrun_the_budget_is_refused(self):
        b, clock = _budget(300)
        b.note_redrive()
        clock.advance(150)  # a real, observed turn duration
        b.note_turn_end()
        assert b.longest_turn_s == 150
        # 150s left, and the evidence says the next turn costs 150s. It
        # does not fit.
        assert b.out_of_time()

    def test_a_turn_that_fits_is_allowed(self):
        b, clock = _budget(300)
        b.note_redrive()
        clock.advance(40)
        b.note_turn_end()
        # 260s left against 40s of evidence — room for more.
        assert not b.out_of_time()

    def test_the_estimate_is_the_worst_turn_seen_not_the_last(self):
        # A fast turn after a slow one must not buy back the budget the
        # slow one proved this session can cost. Judging on the LAST
        # turn (5s) would say there is room for 30 more.
        b, clock = _budget(300)
        b.note_redrive()
        clock.advance(140)
        b.note_turn_end()
        b.note_redrive()
        clock.advance(5)
        b.note_turn_end()
        assert b.longest_turn_s == 140
        assert not b.out_of_time(), '155s left still fits a 140s turn'
        clock.advance(20)
        assert b.out_of_time(), '135s left does not fit a 140s turn'

    def test_the_gate_stops_inside_the_budget_not_past_it(self):
        # The regression, priced. Five attempts at 150s/turn is 750s of
        # an agent's clock spent AFTER the deliverable exists; the run
        # that died had 600s in total. Bounded by time, the gate gives
        # up at 150s with the whole rest of the budget unspent — which
        # is the difference between failing open and being killed.
        redrives = 0
        b, clock = _budget(300)
        for _ in range(5):
            if b.out_of_time():
                break
            b.note_redrive()
            redrives += 1
            clock.advance(150)
            b.note_turn_end()
        assert b.out_of_time()
        assert redrives == 1
        assert clock.t - 1000.0 == 150
        assert b.remaining_s() == 150, 'stopped with budget still on hand'


class TestDisabling:
    def test_zero_budget_means_attempts_only(self):
        # The escape hatch for an operator who wants the old behaviour.
        b, clock = _budget(0)
        b.note_redrive()
        clock.advance(10_000)
        b.note_turn_end()
        assert not b.enabled
        assert not b.out_of_time()

    def test_describe_says_which_bound_is_in_force(self):
        # Whoever reads the fail-open log next needs to know whether the
        # gate gave up or ran out of clock — they are different bugs.
        b, clock = _budget(300)
        assert 'no clock started' in b.describe()
        b.note_redrive()
        clock.advance(150)
        b.note_turn_end()
        text = b.describe()
        assert '300s gate budget' in text
        assert '150s' in text
        off, _ = _budget(0)
        assert 'disabled' in off.describe()


class TestTheBudgetBelongsToTheTurnNotTheSession:
    """A respawn inside one turn inherits the spend.

    The bound above is real per session, and a turn is not one session:
    each re-drive spawns a session, and when the agent falls into a
    degenerate tool loop the circuit breaker kills the session and the
    turn is picked back up in a fresh one. Building fresh gate state per
    session refunded the budget on exactly the respawn the gate had
    provoked — so the gate was bounded at every point and the turn was
    bounded nowhere.

    Seen in CI: a follow-up spent 5/5 re-drives down to "225s left of a
    300s gate budget", the breaker fired, and the next session announced
    "1/5, 300s left of a 300s gate budget". It was still looping 15
    minutes later when the caller's 600s deadline had long passed.
    """

    def setup_method(self):
        reset_ledger('task-1')
        reset_ledger('task-2')

    teardown_method = setup_method

    def test_a_second_session_continues_the_first_ones_spend(self):
        first = ledger_for('task-1', 300.0)
        for _ in range(3):
            first.note_redrive()
            first.note_turn_end()
        assert redrive_count('task-1') == 3

        # The circuit breaker kills that session; the turn is picked up
        # by a new one, which asks for its budget the same way.
        second = ledger_for('task-1', 300.0)
        assert second is first, 'a respawn must not get a fresh budget'
        assert redrive_count('task-1') == 3, 'the spend was refunded'

        second.note_redrive()
        assert redrive_count('task-1') == 4

    def test_the_clock_is_not_rewound_by_a_respawn(self):
        clock = _Clock()
        ledger = ledger_for('task-1', 300.0)
        ledger._now = clock  # noqa: SLF001 — injecting time, as _budget does
        ledger.note_redrive()
        clock.advance(200)
        ledger.note_turn_end()

        again = ledger_for('task-1', 300.0)
        assert again.remaining_s() == pytest.approx(100), (
            'the respawn refunded the wall clock too'
        )
        # 100s left and the longest turn cost 200s — no room for another.
        assert again.out_of_time()

    def test_a_new_turn_starts_over(self):
        ledger = ledger_for('task-1', 300.0)
        for _ in range(5):
            ledger.note_redrive()
            ledger.note_turn_end()
        assert redrive_count('task-1') == 5

        # An orchestrator entry — auto_run_task, execute_planned_task,
        # execute_woken_task, spawn_followup_agent — IS a new turn.
        reset_ledger('task-1')
        assert redrive_count('task-1') == 0
        assert ledger_for('task-1', 300.0) is not ledger

    def test_tasks_do_not_share_a_budget(self):
        a = ledger_for('task-1', 300.0)
        a.note_redrive()
        assert redrive_count('task-1') == 1
        assert redrive_count('task-2') == 0
        assert ledger_for('task-2', 300.0) is not a

    def test_reset_drops_the_entry_so_the_dict_stays_bounded(self):
        ledger_for('task-1', 300.0)
        assert 'task-1' in _ledgers
        reset_ledger('task-1')
        assert 'task-1' not in _ledgers
        # Idempotent: an orchestrator may reset a task that never ran.
        reset_ledger('task-1')


class TestASessionPicksUpTheTurnsBudget:
    """The session-level pin: ``_init_review_gate_state`` must not
    hand a respawn a clean slate.

    This is the regression the CI failure was. The old implementation
    built ``RedriveClock(...)`` fresh here and zeroed a session-local
    ``_review_redrive_count``, so every session for a turn started at
    0/5 with the full wall clock — the refund that made the loop
    unbounded.
    """

    def setup_method(self):
        reset_ledger('task-session')

    teardown_method = setup_method

    def _session(self):
        class _S(_ReviewGateMixin):
            task_id = 'task-session'

        s = _S()
        s._init_review_gate_state()
        return s

    def test_a_respawned_session_is_already_out_of_attempts(self):
        first = self._session()
        for _ in range(REVIEW_REDRIVE_MAX):
            first._note_review_redrive()
            first._note_review_turn_end()
        assert first._review_redrive_exhausted()

        # Circuit breaker kills that session; the turn continues in a
        # new one. It must NOT be allowed another full budget.
        second = self._session()
        assert second._review_redrive_count == REVIEW_REDRIVE_MAX
        assert second._review_redrive_exhausted(), (
            'a respawn got a fresh budget — the turn is unbounded again'
        )

    def test_a_new_turn_gives_the_next_session_a_full_budget(self):
        first = self._session()
        for _ in range(REVIEW_REDRIVE_MAX):
            first._note_review_redrive()
            first._note_review_turn_end()
        assert first._review_redrive_exhausted()

        reset_ledger('task-session')  # an orchestrator entry
        fresh = self._session()
        assert fresh._review_redrive_count == 0
        assert not fresh._review_redrive_exhausted()

    def test_failed_open_is_still_per_session(self):
        # It records what THIS session already shipped, so it must not
        # ride the ledger into the next one.
        first = self._session()
        first._note_review_gate_failed_open()
        assert first._review_gate_failed_open
        assert not self._session()._review_gate_failed_open


class TestTheLedgerIsBounded:
    """``_ledgers`` must not grow one entry per task, forever.

    Review catch on #140: the first version reset only at turn ENTRY,
    which cannot bound the dict — a task that runs one turn and never
    runs again leaves its entry behind for the life of the process, and
    ``_init_review_gate_state`` allocates one for EVERY session whether
    or not a gate ever bites. The terminal points drop it too, which is
    what ``stop_gates.reset_attempts`` has always done and what this was
    supposed to be modelled on.
    """

    def setup_method(self):
        reset_ledger('task-bounded')

    teardown_method = setup_method

    def test_a_turn_that_never_redrives_still_gets_collected(self):
        # Allocation is unconditional: a session asks for its budget
        # before it knows whether a gate will bite.
        ledger_for('task-bounded', 300.0)
        assert 'task-bounded' in _ledgers

        # Whatever ends the task — result persisted or task deleted —
        # must take the entry with it.
        reset_ledger('task-bounded')
        assert 'task-bounded' not in _ledgers

    def test_terminal_cleanup_is_idempotent(self):
        # A task can reach both terminal points (result then delete), or
        # neither (server restart). Both must be free.
        ledger_for('task-bounded', 300.0)
        reset_ledger('task-bounded')
        reset_ledger('task-bounded')
        assert 'task-bounded' not in _ledgers
