"""Tests for User model."""

from datetime import UTC, datetime
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class TestUserModel:
    """Tests for User database model."""

    @pytest.mark.asyncio
    async def test_create_user(self, async_db_session: AsyncSession):
        """Test creating a user."""
        user = User(
            username=f'newuser-{uuid.uuid4().hex[:8]}',
            email=f'newuser-{uuid.uuid4().hex[:8]}@example.com',
            password_hash='hashed_password_here',
            role='user',
            is_active=True,
            created_at=datetime.now(UTC).isoformat(),
        )
        async_db_session.add(user)
        await async_db_session.commit()
        await async_db_session.refresh(user)

        assert user.id is not None
        assert user.password_hash == 'hashed_password_here'
        assert user.is_active is True
        assert user.created_at is not None

    @pytest.mark.asyncio
    async def test_user_relationships(
        self, async_db_session: AsyncSession, test_user: User
    ):
        """Test user can be queried."""
        result = await async_db_session.execute(
            select(User).where(User.id == test_user.id)
        )
        user = result.scalar_one()

        assert user.email == test_user.email
        assert user.username == test_user.username
