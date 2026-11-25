"""A weekly/monthly schedule fires on its day, never every day.

Observed: a weekly schedule created over the API without ``schedule_day``
got a cron trigger of hour+minute only, which APScheduler fires EVERY
DAY; its card read "Weekly Monday 09:00" and its next run was the next
morning, a Thursday. The day is now required by the one rule
(``app.schedule_timing``), and the trigger builder refuses to build a
daily trigger for a weekly or monthly schedule.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.schedule_timing import schedule_day_error
from app.scheduler import cron as cron_mod

pytestmark = pytest.mark.unit

TZ = ZoneInfo('Asia/Shanghai')


def _fires(trigger, start, days):
    fires, prev, now = [], None, start
    end = start + timedelta(days=days)
    while True:
        nxt = trigger.get_next_fire_time(prev, now)
        if nxt is None or nxt >= end:
            return fires
        fires.append(nxt)
        prev, now = nxt, nxt + timedelta(seconds=1)


class TestTheRule:
    @pytest.mark.parametrize('kind', ['weekly', 'monthly'])
    def test_weekly_and_monthly_need_their_day(self, kind):
        error = schedule_day_error(kind, None)
        assert error and 'needs schedule_day' in error

    @pytest.mark.parametrize(
        ('kind', 'day'),
        [('weekly', 0), ('weekly', 8), ('monthly', 0), ('monthly', 32)],
    )
    def test_a_day_out_of_range_is_refused(self, kind, day):
        assert schedule_day_error(kind, day)

    @pytest.mark.parametrize(
        ('kind', 'day'),
        [('weekly', 1), ('weekly', 7), ('monthly', 1), ('monthly', 31)],
    )
    def test_a_day_in_range_is_fine(self, kind, day):
        assert schedule_day_error(kind, day) is None

    @pytest.mark.parametrize('kind', ['minutes', 'hours', 'days'])
    def test_other_types_take_no_day(self, kind):
        assert schedule_day_error(kind, None) is None


class TestTheTrigger:
    @pytest.mark.parametrize('kind', ['weekly', 'monthly'])
    def test_no_day_is_refused_not_turned_into_daily(self, kind):
        with pytest.raises(ValueError, match='needs schedule_day'):
            cron_mod.build_trigger(
                kind, '09:00', schedule_day=None, timezone='Asia/Shanghai'
            )

    def test_weekly_fires_once_a_week_on_its_weekday(self):
        trigger = cron_mod.build_trigger(
            'weekly', '09:00', schedule_day=1, timezone='Asia/Shanghai'
        )
        # Wednesday 2026-11-25, 10:00 local.
        fires = _fires(trigger, datetime(2026, 11, 25, 10, 0, tzinfo=TZ), 21)
        assert [f.isoweekday() for f in fires] == [1, 1, 1]
        assert fires[0] == datetime(2026, 11, 30, 9, 0, tzinfo=TZ)

    def test_monthly_fires_once_a_month_on_its_day(self):
        trigger = cron_mod.build_trigger(
            'monthly', '09:00', schedule_day=5, timezone='Asia/Shanghai'
        )
        fires = _fires(trigger, datetime(2026, 11, 25, 10, 0, tzinfo=TZ), 70)
        assert [(f.month, f.day) for f in fires] == [(12, 5), (1, 5)]
