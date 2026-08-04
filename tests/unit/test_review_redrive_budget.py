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

from app.ai.review_redrive import RedriveClock

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
