"""Unit tests for wait-condition parsing and the wait's deadline."""

from datetime import UTC, datetime, timedelta

import pytest

from app.ai.claude_backend_utils import parse_wait_condition
from app.scheduler.waiting import timeout_error, wait_deadline

pytestmark = pytest.mark.unit


class TestParseWaitCondition:
    def test_basic_block(self):
        """Parse a well-formed wait-condition block."""
        text = (
            'Some result text.\n\n'
            '```wait-condition\n'
            'reason: Waiting for Amazon response\n'
            'keywords: FBAXXX, case response\n'
            'check_strategy: email\n'
            'max_wait_days: 14\n'
            'check_interval_hours: 12\n'
            '```\n'
        )
        result = parse_wait_condition(text)
        assert result is not None
        assert result['reason'] == 'Waiting for Amazon response'
        assert result['keywords'] == ['FBAXXX', 'case response']
        assert result['check_strategy'] == 'email'
        assert result['max_wait_days'] == 14
        assert result['check_interval_hours'] == 12

    def test_no_block(self):
        """Return None when no wait-condition block is present."""
        assert parse_wait_condition('Normal result text.') is None

    def test_missing_reason(self):
        """Return None when reason is missing."""
        text = (
            '```wait-condition\nkeywords: test\ncheck_strategy: manual\n```\n'
        )
        assert parse_wait_condition(text) is None

    def test_defaults(self):
        """Defaults applied for optional fields."""
        text = '```wait-condition\nreason: Waiting for something\n```\n'
        result = parse_wait_condition(text)
        assert result is not None
        assert result['check_strategy'] == 'manual'
        assert result['max_wait_days'] == 30
        assert result['check_interval_hours'] == 24
        assert result['keywords'] == []

    def test_invalid_int_fields(self):
        """Invalid int fields fall back to defaults."""
        text = (
            '```wait-condition\n'
            'reason: test\n'
            'max_wait_days: not_a_number\n'
            'check_interval_hours: abc\n'
            '```\n'
        )
        result = parse_wait_condition(text)
        assert result is not None
        assert result['max_wait_days'] == 30
        assert result['check_interval_hours'] == 24


class TestWaitDeadline:
    """``waiting_since`` + ``max_wait_days``: the moment the checker stops
    the task, and what the card counts down to."""

    def test_deadline_is_the_start_plus_the_max_days(self):
        since = datetime(2026, 11, 24, 9, 14, 24, tzinfo=UTC)
        cond = {'waiting_since': since.isoformat(), 'max_wait_days': 7}
        assert wait_deadline(cond) == since + timedelta(days=7)

    def test_default_max_days_and_a_naive_start_read_as_utc(self):
        cond = {'waiting_since': '2026-11-01T00:00:00'}
        assert wait_deadline(cond) == datetime(2026, 12, 1, tzinfo=UTC)

    @pytest.mark.parametrize(
        'cond',
        [
            {'strategy': 'children'},
            {'waiting_since': 'not a date'},
            {
                'waiting_since': '2026-11-01T00:00:00+00:00',
                'max_wait_days': 'x',
            },
        ],
    )
    def test_no_clock_means_no_deadline(self, cond):
        assert wait_deadline(cond) is None

    def test_timeout_error_says_who_was_waited_for_and_how_to_go_on(self):
        person = timeout_error({'check_strategy': 'manual', 'max_wait_days': 7})
        assert person.startswith('Nobody replied within 7 days')
        assert 'Send a message below to continue' in person
        email = timeout_error({'check_strategy': 'email', 'max_wait_days': 30})
        assert email.startswith('The wait condition was not met within 30')
