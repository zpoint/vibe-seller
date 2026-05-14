"""Tests for the daily task cleanup job (app/scheduler/task_cleanup.py)."""

from datetime import UTC, datetime, timedelta
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.database as _db
from app.models.app_settings import AppSettings
from app.models.base import Base
from app.models.store import Store
from app.models.task import Task
from app.models.user import User
import app.scheduler.task_cleanup as _cleanup
import app.task_delete as _task_delete

pytestmark = pytest.mark.unit


class _StubAgentManager:
    async def stop(self, task_id: str) -> bool:
        return True


@pytest_asyncio.fixture
async def cleanup_env(tmp_path, monkeypatch):
    """In-memory DB + temp tasks dir + stubbed agent manager."""
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
    monkeypatch.setattr(_db, 'async_session', maker)
    monkeypatch.setattr(_cleanup, 'async_session', maker)
    monkeypatch.setattr(_task_delete, 'agent_manager', _StubAgentManager())
    monkeypatch.setattr(_task_delete, 'VIBE_SELLER_DIR', tmp_path)
    yield maker, tmp_path


async def _seed_user_and_store(maker) -> tuple[str, str]:
    """Create a parent user + store row so tasks satisfy FK constraints."""
    user_id = str(uuid.uuid4())
    store_id = str(uuid.uuid4())
    async with maker() as db:
        db.add(
            User(
                id=user_id,
                username='u',
                email='u@e.com',
                password_hash='x',
                role='admin',
                is_active=True,
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        db.add(
            Store(
                id=store_id,
                name='S',
                browser_backend='chrome',
                browser_config='{}',
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
            )
        )
        await db.commit()
    return user_id, store_id


async def _seed_task(
    maker,
    *,
    status: str,
    days_ago: int,
    user_id,
    store_id,
    parent_task_id: str | None = None,
    is_plan_only: bool = False,
) -> str:
    tid = str(uuid.uuid4())
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    async with maker() as db:
        db.add(
            Task(
                id=tid,
                title=f't-{status}-{days_ago}d',
                store_id=store_id,
                parent_task_id=parent_task_id,
                created_by=user_id,
                status=status,
                is_plan_only=is_plan_only,
                created_at=ts,
                updated_at=ts,
            )
        )
        await db.commit()
    return tid


async def _set_retention(maker, days: int) -> None:
    async with maker() as db:
        existing = await db.get(AppSettings, _cleanup.TASK_RETENTION_KEY)
        if existing:
            existing.value = str(days)
        else:
            db.add(
                AppSettings(key=_cleanup.TASK_RETENTION_KEY, value=str(days))
            )
        await db.commit()


@pytest.mark.asyncio
async def test_deletes_old_terminal_tasks(cleanup_env):
    """Completed/failed tasks older than retention go away."""
    maker, _ = cleanup_env
    user_id, store_id = await _seed_user_and_store(maker)
    await _set_retention(maker, 30)

    old_done = await _seed_task(
        maker,
        status='completed',
        days_ago=45,
        user_id=user_id,
        store_id=store_id,
    )
    old_failed = await _seed_task(
        maker, status='failed', days_ago=60, user_id=user_id, store_id=store_id
    )
    deleted = await _cleanup.cleanup_old_tasks()
    assert deleted == 2
    async with maker() as db:
        remaining = (await db.execute(select(Task.id))).scalars().all()
    assert old_done not in remaining
    assert old_failed not in remaining


@pytest.mark.asyncio
async def test_preserves_recent_and_active_tasks(cleanup_env):
    """Recent terminal tasks and active tasks are untouched."""
    maker, _ = cleanup_env
    user_id, store_id = await _seed_user_and_store(maker)
    await _set_retention(maker, 30)

    recent = await _seed_task(
        maker,
        status='completed',
        days_ago=2,
        user_id=user_id,
        store_id=store_id,
    )
    running_old = await _seed_task(
        maker,
        status='running',
        days_ago=120,
        user_id=user_id,
        store_id=store_id,
    )
    pending_old = await _seed_task(
        maker,
        status='pending',
        days_ago=120,
        user_id=user_id,
        store_id=store_id,
    )

    deleted = await _cleanup.cleanup_old_tasks()
    assert deleted == 0
    async with maker() as db:
        remaining = set((await db.execute(select(Task.id))).scalars().all())
    assert {recent, running_old, pending_old}.issubset(remaining)


@pytest.mark.asyncio
async def test_retention_zero_disables_cleanup(cleanup_env):
    """Setting retention to 0 keeps everything."""
    maker, _ = cleanup_env
    user_id, store_id = await _seed_user_and_store(maker)
    await _set_retention(maker, 0)

    await _seed_task(
        maker,
        status='completed',
        days_ago=999,
        user_id=user_id,
        store_id=store_id,
    )
    deleted = await _cleanup.cleanup_old_tasks()
    assert deleted == 0


@pytest.mark.asyncio
async def test_default_retention_when_setting_missing(cleanup_env):
    """No DB row → falls back to 30-day default and trims accordingly."""
    maker, _ = cleanup_env
    user_id, store_id = await _seed_user_and_store(maker)
    # No _set_retention call.

    await _seed_task(
        maker,
        status='completed',
        days_ago=45,
        user_id=user_id,
        store_id=store_id,
    )
    await _seed_task(
        maker,
        status='completed',
        days_ago=10,
        user_id=user_id,
        store_id=store_id,
    )
    deleted = await _cleanup.cleanup_old_tasks()
    assert deleted == 1


@pytest.mark.asyncio
async def test_skips_is_plan_only(cleanup_env):
    """Frozen plan-only tasks (Schedule plan authors) are kept."""
    maker, _ = cleanup_env
    user_id, store_id = await _seed_user_and_store(maker)
    await _set_retention(maker, 30)

    plan_author = await _seed_task(
        maker,
        status='completed',
        days_ago=120,
        user_id=user_id,
        store_id=store_id,
        is_plan_only=True,
    )
    deleted = await _cleanup.cleanup_old_tasks()
    assert deleted == 0
    async with maker() as db:
        assert await db.get(Task, plan_author) is not None


@pytest.mark.asyncio
async def test_skips_parents_only_cleans_leaves(cleanup_env):
    """Cleanup leaves parent rows intact and only removes leaves."""
    maker, _ = cleanup_env
    user_id, store_id = await _seed_user_and_store(maker)
    await _set_retention(maker, 30)

    parent = await _seed_task(
        maker,
        status='completed',
        days_ago=60,
        user_id=user_id,
        store_id=store_id,
    )
    leaf = await _seed_task(
        maker,
        status='completed',
        days_ago=60,
        user_id=user_id,
        store_id=store_id,
        parent_task_id=parent,
    )

    deleted = await _cleanup.cleanup_old_tasks()
    assert deleted == 1
    async with maker() as db:
        # Parent stays (still referenced); leaf is gone.
        assert await db.get(Task, parent) is not None
        assert await db.get(Task, leaf) is None


@pytest.mark.asyncio
async def test_workspace_dir_cleaned(cleanup_env):
    """Workspace dir on disk is removed alongside the row."""
    maker, tmp_path = cleanup_env
    user_id, store_id = await _seed_user_and_store(maker)
    await _set_retention(maker, 30)

    tid = await _seed_task(
        maker,
        status='completed',
        days_ago=45,
        user_id=user_id,
        store_id=store_id,
    )
    task_dir = tmp_path / 'tasks' / tid
    task_dir.mkdir(parents=True)
    (task_dir / 'note.md').write_text('keep-or-not')
    assert task_dir.exists()

    deleted = await _cleanup.cleanup_old_tasks()
    assert deleted == 1
    assert not task_dir.exists()
