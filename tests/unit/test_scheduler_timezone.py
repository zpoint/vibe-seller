"""Unit tests for timezone handling in the scheduler.

Guards against regressing the server-default resolution and the
timezone plumbing in build_trigger, since the function signature
defaults were the source of the original hardcoded 'Asia/Riyadh' bug.
"""

from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytest

from app.scheduler import cron as cron_mod
from app.utils import timezone as tz_mod

pytestmark = pytest.mark.unit


class TestGetServerTimezone:
    def test_returns_iana_string(self):
        tz = tz_mod.get_server_timezone()
        # Must be a non-empty string and parseable by ZoneInfo
        assert isinstance(tz, str) and tz
        ZoneInfo(tz)  # raises if invalid

    def test_falls_back_to_utc_when_tzlocal_raises(self):
        with mock.patch.object(
            tz_mod,
            'get_localzone_name',
            side_effect=RuntimeError('boom'),
        ):
            assert tz_mod.get_server_timezone() == 'UTC'

    def test_falls_back_to_utc_when_tzlocal_returns_empty(self):
        with mock.patch.object(tz_mod, 'get_localzone_name', return_value=''):
            assert tz_mod.get_server_timezone() == 'UTC'


class TestBuildTrigger:
    def test_days_interval_1_uses_cron_with_timezone(self):
        trig = cron_mod.build_trigger(
            'days', '09:30', interval_value=1, timezone='Europe/London'
        )
        assert isinstance(trig, CronTrigger)
        fields = {f.name: str(f) for f in trig.fields}
        assert fields['hour'] == '9'
        assert fields['minute'] == '30'
        # APScheduler stores tz on the trigger itself
        assert str(trig.timezone) == 'Europe/London'

    def test_weekly_with_explicit_timezone(self):
        trig = cron_mod.build_trigger(
            'weekly', '08:00', schedule_day=2, timezone='America/Los_Angeles'
        )
        assert isinstance(trig, CronTrigger)
        assert str(trig.timezone) == 'America/Los_Angeles'

    def test_minutes_interval_uses_interval_trigger(self):
        trig = cron_mod.build_trigger(
            'minutes', '00:00', interval_value=15, timezone='UTC'
        )
        assert isinstance(trig, IntervalTrigger)
        assert trig.interval.total_seconds() == 15 * 60
        assert str(trig.timezone) == 'UTC'

    def test_none_timezone_resolves_to_server_default(self):
        with mock.patch.object(
            cron_mod, 'get_server_timezone', return_value='Asia/Tokyo'
        ):
            trig = cron_mod.build_trigger(
                'days', '09:00', interval_value=1, timezone=None
            )
        assert isinstance(trig, CronTrigger)
        assert str(trig.timezone) == 'Asia/Tokyo'


class TestWeeklyFireDay:
    """Pin the ISO (Mon=1..Sun=7) → APScheduler (Mon=0..Sun=6) mapping.

    The DB stores ``schedule_day`` in ISO form because the frontend
    day picker uses Mon=1..Sun=7. APScheduler's CronTrigger uses
    Mon=0..Sun=6. ``build_trigger`` must translate at the boundary —
    otherwise picking "Monday" in the UI fires on Tuesday, picking
    "Sunday" goes out of range, etc.
    """

    @pytest.mark.parametrize(
        'schedule_day,expected_weekday',
        [
            (1, 'Monday'),
            (2, 'Tuesday'),
            (3, 'Wednesday'),
            (4, 'Thursday'),
            (5, 'Friday'),
            (6, 'Saturday'),
            (7, 'Sunday'),
        ],
    )
    def test_weekly_fires_on_correct_iso_day(
        self, schedule_day, expected_weekday
    ):
        tz = ZoneInfo('Asia/Shanghai')
        trig = cron_mod.build_trigger(
            'weekly',
            '07:00',
            schedule_day=schedule_day,
            timezone='Asia/Shanghai',
        )
        # Reference is a Thursday — far enough back to land on the
        # next occurrence of any weekday without ambiguity.
        ref = datetime(2026, 5, 14, 0, 0, tzinfo=tz)
        next_fire = trig.get_next_fire_time(None, ref)
        assert next_fire is not None
        assert next_fire.strftime('%A') == expected_weekday
        assert next_fire.hour == 7
        assert next_fire.minute == 0

    @pytest.mark.parametrize('bad_day', [0, -1, 8, 100])
    def test_weekly_rejects_out_of_range_day(self, bad_day):
        """Out-of-range schedule_day must fail loudly, not silently
        wrap to a different weekday — the prior ``% 7`` translation
        would have turned 0→Sun, 8→Mon, etc., hiding bad input."""
        with pytest.raises(ValueError, match='1..7'):
            cron_mod.build_trigger(
                'weekly',
                '07:00',
                schedule_day=bad_day,
                timezone='Asia/Shanghai',
            )
