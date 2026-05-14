"""Unit tests for wait-condition parsing."""

import pytest

from app.ai.claude_backend_utils import parse_wait_condition

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
