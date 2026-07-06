"""Fire-cadence + restart-invariance guards for the scheduler.

These pin the behaviour that CI previously never checked: that a
schedule actually fires at its configured wall-clock cadence, and that
the next-fire time does NOT move when the job is rebuilt (which happens
on every server start, since the job store is in-memory).

The original bug: ``build_trigger`` returned a bare ``IntervalTrigger``
for ``minutes``/``hours``/``days>1`` with no ``start_date``. APScheduler
then anchored the first fire to ``now + interval`` at job-add time, so:
  * the configured HH:MM was ignored (a 04:00 "every 3 days" fired at
    whatever o'clock the server booted), and
  * every restart re-anchored the countdown to ``restart + interval`` —
    an interval longer than the restart cadence therefore NEVER elapsed
    and the schedule fired exactly zero times after creation.

We drive ``trigger.get_next_fire_time`` with a simulated clock (exactly
what APScheduler does internally), which makes "mock time passing"
deterministic and clock-free. One live-scheduler test then confirms an
anchored interval trigger really fires and runs its side effect.
"""

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import pytest

from app.scheduler import cron as cron_mod

pytestmark = pytest.mark.unit

TZ_NAME = 'Asia/Shanghai'
TZ = ZoneInfo(TZ_NAME)
# A fixed creation anchor at an awkward time-of-day (12:20) — proves the
# fire time is pinned to the configured HH:MM, not inherited from when
# the schedule happened to be created / the server happened to boot.
ANCHOR = datetime(2026, 6, 22, 12, 20, 15, tzinfo=TZ)


def _fires_in_window(trigger, start_now, window):
    """Enumerate the fire times a trigger produces as the clock advances.

    Mirrors APScheduler's own loop: after each fire, the simulated clock
    jumps to that fire time and asks for the next one. Returns every fire
    at or before ``start_now + window``.
    """
    fires = []
    prev = None
    now = start_now
    end = start_now + window
    # Bound the loop defensively so a broken (never-advancing) trigger
    # can't spin forever.
    for _ in range(10_000):
        nxt = trigger.get_next_fire_time(prev, now)
        if nxt is None or nxt > end:
            break
        fires.append(nxt)
        prev = nxt
        now = nxt
    return fires


class TestFiresAtConfiguredCadence:
    """Simulated time passes → the schedule fires on the right grid."""

    def test_every_3_days_fires_at_configured_hhmm_every_3_days(self):
        trig = cron_mod.build_trigger(
            'days', '04:00', interval_value=3, timezone=TZ_NAME, anchor=ANCHOR
        )
        start = datetime(2026, 6, 23, 9, 0, tzinfo=TZ)
        fires = _fires_in_window(trig, start, timedelta(days=30))

        assert len(fires) >= 8, fires
        # Every fire lands at the configured 04:00 — not at boot o'clock.
        assert all(f.hour == 4 and f.minute == 0 for f in fires), fires
        # ...and consecutive fires are exactly 3 days apart.
        gaps = {(b - a) for a, b in zip(fires, fires[1:])}
        assert gaps == {timedelta(days=3)}, gaps

    def test_hourly_fires_every_hour(self):
        trig = cron_mod.build_trigger(
            'hours', '00:00', interval_value=1, timezone=TZ_NAME, anchor=ANCHOR
        )
        start = datetime(2026, 6, 23, 9, 5, tzinfo=TZ)
        fires = _fires_in_window(trig, start, timedelta(hours=24))

        assert len(fires) >= 23, fires
        gaps = {(b - a) for a, b in zip(fires, fires[1:])}
        assert gaps == {timedelta(hours=1)}, gaps

    def test_daily_fires_once_a_day_at_hhmm(self):
        trig = cron_mod.build_trigger(
            'days', '04:00', interval_value=1, timezone=TZ_NAME, anchor=ANCHOR
        )
        start = datetime(2026, 6, 23, 9, 0, tzinfo=TZ)
        fires = _fires_in_window(trig, start, timedelta(days=7))

        assert len(fires) == 7, fires
        assert all(f.hour == 4 and f.minute == 0 for f in fires), fires
        gaps = {(b - a) for a, b in zip(fires, fires[1:])}
        assert gaps == {timedelta(days=1)}, gaps

    def test_weekly_fires_once_a_week_on_configured_day(self):
        # schedule_day=1 → Monday (ISO). 2026-06-22 is a Monday.
        trig = cron_mod.build_trigger(
            'weekly', '04:00', schedule_day=1, timezone=TZ_NAME, anchor=ANCHOR
        )
        start = datetime(2026, 6, 23, 9, 0, tzinfo=TZ)
        fires = _fires_in_window(trig, start, timedelta(days=28))

        assert len(fires) == 4, fires
        assert all(f.strftime('%A') == 'Monday' for f in fires), fires
        assert all(f.hour == 4 for f in fires), fires
        gaps = {(b - a) for a, b in zip(fires, fires[1:])}
        assert gaps == {timedelta(days=7)}, gaps


class TestRestartInvariance:
    """Rebuilding the job (== a server restart) must not move next-fire.

    This is the core regression guard. Before the fix, rebuilding an
    interval trigger re-anchored its first fire to ``now + interval``,
    so frequent restarts pushed a long-interval schedule's next fire
    perpetually into the future and it never fired.
    """

    @pytest.mark.parametrize(
        'kind,kwargs',
        [
            ('hours', {'schedule_type': 'hours', 'interval_value': 6}),
            ('daily', {'schedule_type': 'days', 'interval_value': 1}),
            ('every_3_days', {'schedule_type': 'days', 'interval_value': 3}),
            (
                'weekly',
                {'schedule_type': 'weekly', 'schedule_day': 1},
            ),
        ],
    )
    def test_next_fire_is_identical_across_rebuilds(self, kind, kwargs):
        # First build (initial job registration) and first-fire query.
        trig_a = cron_mod.build_trigger(
            schedule_time='04:00', timezone=TZ_NAME, anchor=ANCHOR, **kwargs
        )
        now0 = datetime(2026, 6, 23, 3, 0, tzinfo=TZ)
        fire_a = trig_a.get_next_fire_time(None, now0)
        assert fire_a is not None

        # Simulate a later server restart that lands STILL BEFORE that
        # fire (halfway to it) — rebuild the job from the same schedule
        # row and re-query. The pending fire must not have drifted.
        # (Advancing past the fire would legitimately move it; that's
        # firing, not drift, so we stay before it.)
        trig_b = cron_mod.build_trigger(
            schedule_time='04:00', timezone=TZ_NAME, anchor=ANCHOR, **kwargs
        )
        now1 = now0 + (fire_a - now0) / 2
        fire_b = trig_b.get_next_fire_time(None, now1)

        assert fire_b is not None
        assert fire_a == fire_b, (kind, fire_a, fire_b)

    def test_repeated_restarts_never_starve_a_long_interval(self):
        """A 3-day schedule restarted every few hours still fires.

        Reproduces the reported failure directly: without the anchor,
        each rebuild reset the 3-day clock and the fire never arrived.
        """
        first_fire = None
        now = datetime(2026, 6, 23, 3, 0, tzinfo=TZ)
        # Restart every 5 hours for ~4 simulated days.
        for _ in range(20):
            trig = cron_mod.build_trigger(
                'days',
                '04:00',
                interval_value=3,
                timezone=TZ_NAME,
                anchor=ANCHOR,
            )
            nxt = trig.get_next_fire_time(None, now)
            if first_fire is None:
                first_fire = nxt
                assert first_fire is not None
            # While the clock hasn't reached the fire, restarts must NOT
            # push it out (the old bug did exactly that). Once the clock
            # crosses it, it has fired — stop asserting.
            if now < first_fire:
                assert nxt == first_fire, (now, nxt, first_fire)
            now += timedelta(hours=5)
        # The clock eventually crosses the fire — i.e. it really fires,
        # instead of being perpetually deferred by the restarts.
        assert now > first_fire


class TestLiveSchedulerActuallyFires:
    """End-to-end: an anchored interval trigger fires under a real
    AsyncIOScheduler and runs its side effect (writes a file)."""

    async def test_anchored_interval_job_runs_and_writes_file(self, tmp_path):
        marker = tmp_path / 'fired.txt'
        fired = asyncio.Event()

        def job():
            marker.write_text('fired')
            fired.set()

        sched = AsyncIOScheduler()
        # start_date in the immediate past → the 1s grid's next point is
        # ~1s away; the same anchoring pattern build_trigger now uses.
        start = datetime.now(TZ) - timedelta(seconds=10)
        trig = IntervalTrigger(seconds=1, start_date=start, timezone=TZ)
        sched.add_job(job, trig, id='fire-test', max_instances=1)
        sched.start()
        try:
            await asyncio.wait_for(fired.wait(), timeout=5.0)
        finally:
            sched.shutdown(wait=False)

        assert marker.read_text() == 'fired'
