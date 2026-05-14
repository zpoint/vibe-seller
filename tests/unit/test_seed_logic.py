"""Unit tests for database seed logic in init_db().

Each test creates a fresh in-memory SQLite, runs init_db (or the
seed portion), and verifies the resulting DB state.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import init_db
from app.models.app_settings import AppSettings
from app.models.base import Base
from app.models.user import User
from app.password import verify_password

pytestmark = pytest.mark.unit

# Well-known IDs from app/config.py
DEFAULT_USER_ID = '00000000-0000-0000-0000-000000000001'
AI_BOT_USER_ID = '00000000-0000-0000-0000-000000000002'


async def _run_seed(monkeypatch, env_overrides=None):
    """Create a fresh DB and run init_db seed logic."""
    # Set env defaults
    monkeypatch.delenv('ADMIN_EMAIL', raising=False)
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    monkeypatch.delenv('VIBE_AUTH_REQUIRED', raising=False)
    monkeypatch.delenv('FORCE_ADMIN_RESET', raising=False)

    for k, v in (env_overrides or {}).items():
        monkeypatch.setenv(k, v)

    engine = create_async_engine(
        'sqlite+aiosqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # Patch the module-level async_session and engine used by
    # init_db so it writes to our test DB.
    monkeypatch.setattr('app.database.engine', engine)
    monkeypatch.setattr('app.database.async_session', maker)

    await init_db()
    return engine, maker


class TestFreshDb:
    async def test_fresh_db_default_env(self, monkeypatch):
        _, maker = await _run_seed(monkeypatch)
        async with maker() as db:
            admin = await db.get(User, DEFAULT_USER_ID)
            assert admin is not None
            assert admin.email == 'admin@vibe-seller.local'
            assert admin.role == 'admin'
            assert verify_password('admin', admin.password_hash)

            auth = await db.get(AppSettings, 'auth_required')
            assert auth is not None
            assert auth.value == 'false'

    async def test_fresh_db_custom_env(self, monkeypatch):
        _, maker = await _run_seed(
            monkeypatch,
            {
                'ADMIN_EMAIL': 'custom@test.com',
                'ADMIN_PASSWORD': 'secret123',
                'VIBE_AUTH_REQUIRED': 'true',
            },
        )
        async with maker() as db:
            admin = await db.get(User, DEFAULT_USER_ID)
            assert admin.email == 'custom@test.com'
            assert verify_password('secret123', admin.password_hash)

            auth = await db.get(AppSettings, 'auth_required')
            assert auth.value == 'true'

    async def test_ai_bot_user_seeded(self, monkeypatch):
        _, maker = await _run_seed(monkeypatch)
        async with maker() as db:
            bot = await db.get(User, AI_BOT_USER_ID)
            assert bot is not None
            assert bot.role == 'ai_bot'


class TestExistingAdmin:
    async def test_no_flag_env_applies(self, monkeypatch):
        """Admin exists but admin_credentials_set is not set.

        Env vars should update admin credentials.
        """
        _, maker = await _run_seed(monkeypatch)

        # Re-run with different env
        monkeypatch.setenv('ADMIN_EMAIL', 'updated@test.com')
        monkeypatch.setenv('ADMIN_PASSWORD', 'newpass')
        await init_db()

        async with maker() as db:
            admin = await db.get(User, DEFAULT_USER_ID)
            assert admin.email == 'updated@test.com'
            assert verify_password('newpass', admin.password_hash)

    async def test_flag_set_env_ignored(self, monkeypatch):
        """admin_credentials_set=true → env vars do NOT update."""
        _, maker = await _run_seed(monkeypatch)

        # Set the flag
        async with maker() as db:
            db.add(AppSettings(key='admin_credentials_set', value='true'))
            await db.commit()

        # Re-run with different env
        monkeypatch.setenv('ADMIN_EMAIL', 'ignored@test.com')
        monkeypatch.setenv('ADMIN_PASSWORD', 'ignored')
        await init_db()

        async with maker() as db:
            admin = await db.get(User, DEFAULT_USER_ID)
            # Should still have the original email
            assert admin.email == 'admin@vibe-seller.local'

    async def test_force_reset_overrides_flag(self, monkeypatch):
        """FORCE_ADMIN_RESET=true overrides admin_credentials_set."""
        _, maker = await _run_seed(monkeypatch)

        # Set the flag
        async with maker() as db:
            db.add(AppSettings(key='admin_credentials_set', value='true'))
            await db.commit()

        # Force reset with new creds
        monkeypatch.setenv('ADMIN_EMAIL', 'reset@test.com')
        monkeypatch.setenv('ADMIN_PASSWORD', 'resetpass')
        monkeypatch.setenv('FORCE_ADMIN_RESET', 'true')
        await init_db()

        async with maker() as db:
            admin = await db.get(User, DEFAULT_USER_ID)
            assert admin.email == 'reset@test.com'
            assert verify_password('resetpass', admin.password_hash)
            # Flag should be deleted
            flag = await db.get(AppSettings, 'admin_credentials_set')
            assert flag is None


class TestAuthRequiredSticky:
    async def test_auth_required_not_re_seeded(self, monkeypatch):
        """Once auth_required is set, env var doesn't overwrite it."""
        _, maker = await _run_seed(monkeypatch)

        # Manually set to 'true' (simulating UI toggle)
        async with maker() as db:
            auth = await db.get(AppSettings, 'auth_required')
            auth.value = 'true'
            await db.commit()

        # Re-run with default env (VIBE_AUTH_REQUIRED=false)
        monkeypatch.delenv('VIBE_AUTH_REQUIRED', raising=False)
        await init_db()

        async with maker() as db:
            auth = await db.get(AppSettings, 'auth_required')
            # Should still be 'true' — not overwritten
            assert auth.value == 'true'


class TestScheduleStatePK:
    """schedule_state PK must include store_id so sibling fanout tasks
    don't clobber each other's cursor. Now enforced by the SQLAlchemy
    model itself — `create_all` produces the right shape on every
    fresh install, no ad-hoc rebuild needed.
    """

    async def test_fresh_install_has_composite_pk(self, monkeypatch):
        monkeypatch.delenv('ADMIN_EMAIL', raising=False)
        monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
        monkeypatch.delenv('VIBE_AUTH_REQUIRED', raising=False)
        monkeypatch.delenv('FORCE_ADMIN_RESET', raising=False)

        engine = create_async_engine(
            'sqlite+aiosqlite://',
            connect_args={'check_same_thread': False},
            poolclass=StaticPool,
            echo=False,
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        monkeypatch.setattr('app.database.engine', engine)
        monkeypatch.setattr('app.database.async_session', maker)

        await init_db()

        async with engine.connect() as conn:
            cols = list(
                await conn.execute(text('PRAGMA table_info(schedule_state)'))
            )
            col_pks = {row[1]: row[5] for row in cols}
            assert col_pks['schedule_id'] > 0
            assert col_pks['store_id'] > 0
            assert col_pks['key'] > 0
