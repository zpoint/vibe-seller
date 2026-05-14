"""Unit tests for username/email schema and model consistency.

Guards against the original bug where 'email' did double duty as
login identifier, causing user lockout.
"""

from pydantic import ValidationError
import pytest

from app.models.user import User
from app.schemas.user import (
    LoginRequest,
    ProfileUpdate,
    UserCreate,
    UserResponse,
    UserUpdate,
)

pytestmark = pytest.mark.unit


# ── Model column checks ────────────────────────────────


class TestUserModel:
    def test_has_username_column(self):
        cols = {c.name for c in User.__table__.columns}
        assert 'username' in cols

    def test_has_email_column(self):
        cols = {c.name for c in User.__table__.columns}
        assert 'email' in cols

    def test_email_is_nullable(self):
        col = User.__table__.columns['email']
        assert col.nullable is True

    def test_username_is_unique(self):
        col = User.__table__.columns['username']
        assert col.unique is True


# ── Schema field checks ────────────────────────────────


class TestLoginRequest:
    def test_has_identifier_field(self):
        assert 'identifier' in LoginRequest.model_fields

    def test_no_email_field(self):
        """LoginRequest must NOT have an 'email' field."""
        assert 'email' not in LoginRequest.model_fields


class TestUserCreateSchema:
    def test_has_username(self):
        assert 'username' in UserCreate.model_fields

    def test_has_email(self):
        assert 'email' in UserCreate.model_fields


class TestUserResponseSchema:
    def test_has_username(self):
        assert 'username' in UserResponse.model_fields

    def test_has_email(self):
        assert 'email' in UserResponse.model_fields


class TestUserUpdateSchema:
    def test_has_username(self):
        assert 'username' in UserUpdate.model_fields

    def test_has_email(self):
        assert 'email' in UserUpdate.model_fields


class TestProfileUpdateSchema:
    def test_has_username(self):
        assert 'username' in ProfileUpdate.model_fields


# ── Email validation ───────────────────────────────────


class TestEmailValidation:
    def test_rejects_non_email(self):
        with pytest.raises(ValidationError):
            UserCreate(
                username='test',
                email='notanemail',
                password='pw',
            )

    def test_accepts_valid_email(self):
        u = UserCreate(
            username='test',
            email='a@b.com',
            password='pw',
        )
        assert u.email == 'a@b.com'

    def test_email_optional(self):
        u = UserCreate(username='test', password='pw')
        assert u.email is None


# ── Username validation ────────────────────────────────


class TestUsernameValidation:
    def test_rejects_at_sign(self):
        with pytest.raises(ValidationError):
            UserCreate(
                username='bad@name',
                password='pw',
            )

    def test_rejects_spaces(self):
        with pytest.raises(ValidationError):
            UserCreate(
                username='bad name',
                password='pw',
            )

    def test_rejects_too_short(self):
        with pytest.raises(ValidationError):
            UserCreate(username='ab', password='pw')

    def test_lowercases(self):
        u = UserCreate(username='MyUser', password='pw')
        assert u.username == 'myuser'

    def test_accepts_valid(self):
        u = UserCreate(username='my-user_1', password='pw')
        assert u.username == 'my-user_1'
