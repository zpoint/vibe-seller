"""Unit tests for timezone handling in the scheduler.

Guards against regressing the server-default resolution and the
timezone plumbing in build_trigger, since the function signature
defaults were the source of the original hardcoded 'Asia/Riyadh' bug.
"""

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
