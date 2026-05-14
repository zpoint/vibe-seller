"""Tests for Task model."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.store import Store
from app.models.task import Task
from app.models.user import User


class TestTaskModel:
    """Tests for Task database model."""

    @pytest.mark.asyncio
    async def test_create_task(
        self, async_db_session: AsyncSession, test_store: Store, test_user: User
    ):
        """Test creating a task."""
        task = Task(
            title='Test Task',
            description='Test description',
            store_id=test_store.id,
            created_by=test_user.id,
            status='pending',
            created_at=datetime.now(UTC).isoformat(),
            updated_at=datetime.now(UTC).isoformat(),
        )
        async_db_session.add(task)
        await async_db_session.commit()
        await async_db_session.refresh(task)

        assert task.id is not None
        assert task.title == 'Test Task'
        assert task.description == 'Test description'
        assert task.store_id == test_store.id
        assert task.created_by == test_user.id
        assert task.status == 'pending'
        assert task.created_at is not None
        assert task.updated_at is not None

    @pytest.mark.asyncio
    async def test_task_status_transitions(
        self, async_db_session: AsyncSession, test_task: Task
    ):
        """Test task status can be updated."""

        # Start with pending
        assert test_task.status == 'pending'

        # Move to running
        test_task.status = 'running'
        test_task.updated_at = datetime.now(UTC).isoformat()
        await async_db_session.commit()
        await async_db_session.refresh(test_task)
        assert test_task.status == 'running'

        # Move to completed
        test_task.status = 'completed'
        test_task.completed_at = datetime.now(UTC).isoformat()
        test_task.updated_at = datetime.now(UTC).isoformat()
        await async_db_session.commit()
        await async_db_session.refresh(test_task)
        assert test_task.status == 'completed'

    @pytest.mark.asyncio
    async def test_task_without_store(
        self, async_db_session: AsyncSession, test_user: User
    ):
        """Test creating a task without a store."""

        task = Task(
            title='Independent Task',
            description='No store needed',
            created_by=test_user.id,
            status='pending',
            created_at=datetime.now(UTC).isoformat(),
            updated_at=datetime.now(UTC).isoformat(),
        )
        async_db_session.add(task)
        await async_db_session.commit()
        await async_db_session.refresh(task)

        assert task.store_id is None
        assert task.title == 'Independent Task'
