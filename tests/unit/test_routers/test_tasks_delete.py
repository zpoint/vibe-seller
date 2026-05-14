"""Tests for DELETE /api/tasks/{id} and the underlying delete helper."""

from datetime import UTC, datetime
import uuid

from httpx import AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.screenshot import Screenshot
from app.models.task import Task
from app.models.task_attachment import TaskAttachment
from app.models.task_log import TaskLog
from app.models.task_message import TaskMessage
from app.models.task_step import TaskStep
from app.task_delete import delete_task as delete_task_helper

pytestmark = pytest.mark.unit


class _StubAgentManager:
    def __init__(self):
        self.stop_calls: list[str] = []

    async def stop(self, task_id: str) -> bool:
        self.stop_calls.append(task_id)
        return True


@pytest.fixture(autouse=True)
def stub_agent_manager(monkeypatch):
    """Avoid hitting the real Claude backend in delete tests."""
    stub = _StubAgentManager()
    monkeypatch.setattr('app.task_delete.agent_manager', stub)
    return stub


@pytest.fixture
def isolated_tasks_dir(tmp_path, monkeypatch):
    """Point the task workspace dir helper at a temp tree."""
    monkeypatch.setattr('app.task_delete.VIBE_SELLER_DIR', tmp_path)
    return tmp_path / 'tasks'


class TestDeleteTaskEndpoint:
    @pytest.mark.asyncio
    async def test_delete_pending_task(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
        test_task: Task,
        isolated_tasks_dir,
    ):
        """Pending task: row gone, dependent rows gone, dir gone."""
        task_dir = isolated_tasks_dir / test_task.id
        task_dir.mkdir(parents=True)
        (task_dir / 'note.md').write_text('hi')

        # Add a dependent row to verify cleanup.
        async_db_session.add(
            TaskStep(
                task_id=test_task.id,
                step_index=0,
                name='click button',
                action_type='click',
                status='completed',
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        await async_db_session.commit()

        r = await authenticated_client.delete(f'/api/tasks/{test_task.id}')
        assert r.status_code == 200
        assert r.json() == {'ok': True}
        assert not task_dir.exists()

        assert (
            await async_db_session.execute(
                select(Task).where(Task.id == test_task.id)
            )
        ).first() is None
        assert (
            await async_db_session.execute(
                select(TaskStep).where(TaskStep.task_id == test_task.id)
            )
        ).first() is None

    @pytest.mark.asyncio
    async def test_delete_running_task_blocked(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
        test_task: Task,
    ):
        """Running task can't be deleted — user must stop first."""
        test_task.status = 'running'
        await async_db_session.commit()

        r = await authenticated_client.delete(f'/api/tasks/{test_task.id}')
        assert r.status_code == 409

        # Row still present.
        kept = await async_db_session.get(Task, test_task.id)
        assert kept is not None

    @pytest.mark.asyncio
    async def test_delete_unknown_task_idempotent(
        self, authenticated_client: AsyncClient
    ):
        r = await authenticated_client.delete(f'/api/tasks/{uuid.uuid4()}')
        assert r.status_code == 200
        assert r.json() == {'ok': True}

    @pytest.mark.asyncio
    async def test_delete_requires_auth(
        self, async_client: AsyncClient, test_task: Task
    ):
        r = await async_client.delete(f'/api/tasks/{test_task.id}')
        assert r.status_code == 401


class TestDeleteHelper:
    @pytest.mark.asyncio
    async def test_helper_cascade_deletes_subtree(
        self,
        async_db_session: AsyncSession,
        test_task: Task,
        isolated_tasks_dir,
    ):
        """Deleting a parent removes the whole tree (children, grandchildren)."""
        child_id = str(uuid.uuid4())
        grandchild_id = str(uuid.uuid4())
        async_db_session.add_all([
            Task(
                id=child_id,
                title='child',
                store_id=test_task.store_id,
                parent_task_id=test_task.id,
                created_by=test_task.created_by,
                status='completed',
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
            ),
            Task(
                id=grandchild_id,
                title='grandchild',
                store_id=test_task.store_id,
                parent_task_id=child_id,
                created_by=test_task.created_by,
                status='completed',
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
            ),
        ])
        await async_db_session.commit()

        # Workspace dirs for each — confirm they're all swept.
        for tid in (test_task.id, child_id, grandchild_id):
            d = isolated_tasks_dir / tid
            d.mkdir(parents=True, exist_ok=True)
            (d / 'note.md').write_text('hi')

        ok = await delete_task_helper(async_db_session, test_task.id)
        assert ok is True

        for tid in (test_task.id, child_id, grandchild_id):
            assert await async_db_session.get(Task, tid) is None
            assert not (isolated_tasks_dir / tid).exists()

    @pytest.mark.asyncio
    async def test_helper_refuses_when_descendant_active(
        self,
        async_db_session: AsyncSession,
        test_task: Task,
        isolated_tasks_dir,
    ):
        """If any descendant is RUNNING, the whole delete refuses."""
        child = Task(
            id=str(uuid.uuid4()),
            title='child',
            store_id=test_task.store_id,
            parent_task_id=test_task.id,
            created_by=test_task.created_by,
            status='running',
            created_at=datetime.now(UTC).isoformat(),
            updated_at=datetime.now(UTC).isoformat(),
        )
        async_db_session.add(child)
        await async_db_session.commit()

        with pytest.raises(ValueError):
            await delete_task_helper(async_db_session, test_task.id)
        # Both rows still present.
        assert await async_db_session.get(Task, test_task.id) is not None
        assert await async_db_session.get(Task, child.id) is not None

    @pytest.mark.asyncio
    async def test_helper_deletes_all_dependent_rows(
        self,
        async_db_session: AsyncSession,
        test_task: Task,
        isolated_tasks_dir,
    ):
        async_db_session.add_all([
            TaskStep(
                task_id=test_task.id,
                step_index=0,
                name='click button',
                action_type='click',
                status='completed',
                created_at=datetime.now(UTC).isoformat(),
            ),
            TaskMessage(
                task_id=test_task.id,
                role='user',
                content='hi',
                seq=0,
                created_at=datetime.now(UTC).isoformat(),
            ),
            TaskAttachment(
                task_id=test_task.id,
                file_name='x.txt',
                file_path='/tmp/x.txt',
                file_type='text/plain',
                file_size=1,
                created_at=datetime.now(UTC).isoformat(),
            ),
            TaskLog(
                task_id=test_task.id,
                log_type='info',
                content='hi',
                timestamp_ms=0,
            ),
            Screenshot(
                task_id=test_task.id,
                file_path='/tmp/shot.png',
                created_at=datetime.now(UTC).isoformat(),
            ),
        ])
        await async_db_session.commit()

        ok = await delete_task_helper(async_db_session, test_task.id)
        assert ok is True

        for model in (
            TaskStep,
            TaskMessage,
            TaskAttachment,
            TaskLog,
            Screenshot,
        ):
            rows = (
                await async_db_session.execute(
                    select(model).where(model.task_id == test_task.id)
                )
            ).first()
            assert rows is None, f'{model.__name__} rows not cleaned up'
