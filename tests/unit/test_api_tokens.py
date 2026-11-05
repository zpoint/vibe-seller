"""Unit tests for personal API tokens — generation, hashing, validity.

The plaintext of a token exists for exactly one moment: the response
that created it. What the database keeps is its SHA-256 hash, and the
lookup key is that hash. These tests pin that contract, plus the
expiry / revocation / throttle rules that decide whether a presented
token authenticates at all.
"""

from datetime import UTC, datetime, timedelta
import hashlib

import pytest

from app.api_tokens import (
    DEFAULT_EXPIRY_DAYS,
    DISPLAY_PREFIX_LEN,
    LAST_USED_THROTTLE,
    TOKEN_PREFIX,
    display_prefix,
    expiry_for_days,
    generate_token,
    hash_token,
    is_expired,
    is_revoked,
    should_touch,
)
from app.models.api_token import ApiToken

pytestmark = pytest.mark.unit


def _row(**kwargs) -> ApiToken:
    """A detached ApiToken row (no DB needed for the pure rules)."""
    defaults = {
        'id': 'tok-1',
        'user_id': 'user-1',
        'name': 'laptop',
        'token_hash': 'deadbeef',
        'prefix': 'vst_abcdefgh',
        'created_at': datetime.now(UTC).isoformat(),
        'last_used_at': None,
        'expires_at': None,
        'revoked_at': None,
    }
    defaults.update(kwargs)
    return ApiToken(**defaults)


class TestGeneration:
    def test_token_starts_with_the_marker_prefix(self):
        token = generate_token()
        assert token.startswith(TOKEN_PREFIX)
        assert TOKEN_PREFIX == 'vst_'

    def test_token_carries_enough_entropy(self):
        """32 random bytes → 43 base64url chars after the marker."""
        token = generate_token()
        assert len(token) == len(TOKEN_PREFIX) + 43

    def test_tokens_are_unique(self):
        assert len({generate_token() for _ in range(50)}) == 50


class TestHashing:
    def test_hash_is_the_sha256_hex_of_the_plaintext(self):
        token = generate_token()
        assert (
            hash_token(token)
            == hashlib.sha256(token.encode('utf-8')).hexdigest()
        )

    def test_hash_is_stable_and_not_the_plaintext(self):
        token = generate_token()
        digest = hash_token(token)
        assert digest == hash_token(token)
        assert digest != token
        assert len(digest) == 64

    def test_different_tokens_hash_differently(self):
        assert hash_token(generate_token()) != hash_token(generate_token())


class TestDisplayPrefix:
    def test_prefix_keeps_only_the_head_of_the_token(self):
        token = generate_token()
        prefix = display_prefix(token)
        assert prefix == token[:DISPLAY_PREFIX_LEN]
        assert len(prefix) == 12
        # The stored prefix must not carry the secret.
        assert token not in prefix
        assert len(prefix) < len(token)


class TestExpiry:
    def test_none_means_never_expires(self):
        assert expiry_for_days(None) is None

    def test_days_are_added_to_now(self):
        now = datetime.now(UTC)
        expiry = expiry_for_days(90, now)
        assert expiry is not None
        parsed = datetime.fromisoformat(expiry)
        assert abs((parsed - (now + timedelta(days=90))).total_seconds()) < 1

    def test_default_is_ninety_days(self):
        assert DEFAULT_EXPIRY_DAYS == 90

    def test_token_without_expiry_never_expires(self):
        assert is_expired(_row(expires_at=None)) is False

    def test_token_with_future_expiry_is_live(self):
        future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        assert is_expired(_row(expires_at=future)) is False

    def test_token_with_past_expiry_is_expired(self):
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        assert is_expired(_row(expires_at=past)) is True

    def test_unreadable_expiry_fails_closed(self):
        """A deadline we cannot read must not widen access."""
        assert is_expired(_row(expires_at='not-a-timestamp')) is True


class TestRevocation:
    def test_live_token_is_not_revoked(self):
        assert is_revoked(_row(revoked_at=None)) is False

    def test_revoked_token_is_revoked(self):
        stamp = datetime.now(UTC).isoformat()
        assert is_revoked(_row(revoked_at=stamp)) is True


class TestLastUsedThrottle:
    def test_never_used_is_stamped(self):
        assert should_touch(None) is True

    def test_recent_use_is_not_stamped_again(self):
        recent = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
        assert should_touch(recent) is False

    def test_old_use_is_stamped_again(self):
        old = (
            datetime.now(UTC) - LAST_USED_THROTTLE - timedelta(seconds=1)
        ).isoformat()
        assert should_touch(old) is True

    def test_throttle_window_is_one_minute(self):
        assert LAST_USED_THROTTLE == timedelta(minutes=1)
