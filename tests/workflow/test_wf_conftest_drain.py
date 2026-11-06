"""The fixture drains the app's background work before the database goes.

A test that launches a background pipeline and returns immediately
leaves that pipeline running. The engine fixture drops the database
at teardown — so without a drain the writer writes to a dropped
database, the shared connection is torn down under it, and the
teardown itself ERRORS ("Cannot operate on a closed database" at
``drop_all``). pytest reports that against the test that leaked the
writer, even though the writer belongs to none of them.

With the drain, the teardown waits for the writer: the drop finds an
idle database, and the next test starts clean.
"""

import asyncio

import pytest
from sqlalchemy import text

from app.models.task import Task
from tests.workflow.conftest import drain_background_tasks, real_sleep

pytestmark = pytest.mark.workflow


async def _background_writer(session_maker, task_id: str) -> None:
    """A writer that holds a database session while it works.

    This is what the app's own pipelines do (auto_run_task, the
    follow-up spawn): they run off the request path and write to the
    database while the test that launched them is already over.
    """
    async with session_maker() as db:
        # A long query: the connection is BUSY inside the call when
        # the teardown gets to the writer — which is what a cancel
        # would cut short (SQLAlchemy discards a connection whose
        # call was cut short). The drain lets the call finish.
        await db.execute(
            text(
                'WITH RECURSIVE c(x) AS ('
                '  SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 8000000'
                ') SELECT count(*) FROM c'
            )
        )
        task = await db.get(Task, task_id)
        if task is not None:
            task.transcript_tail = 'written after the test returned'
            await db.commit()


class TestFixtureDrain:
    async def test_a_leaked_writer_is_drained_at_teardown(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
    ):
        """Launch a writer and return immediately — the teardown copes.

        The teardown must wait for the writer before it drops the
        database. If it does not, the writer's write lands on a
        dropped database and the teardown errors on the closed
        connection — a failure that has nothing to do with the test
        that leaked it.
        """
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Leaky writer',
                'description': 'x',
                'defer_start': True,
            },
        )
        assert r.status_code == 200, r.text
        task_id = r.json()['id']

        # The writer runs off the request path, and this test returns
        # while it is still working.
        task = asyncio.create_task(
            _background_writer(override_async_session, task_id)
        )
        # Give it a moment to be INSIDE its session, so the teardown
        # has something real to wait for.
        await real_sleep(0.05)
        assert not task.done()

    async def test_the_next_test_starts_clean(
        self, admin_client, install_fake_agent, override_async_session
    ):
        """The test after a leaky one gets a usable database.

        The point of the drain is not the leaking test's own teardown
        — it is that the NEXT test (in this file or any other) is not
        handed a half-dropped database.
        """
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Another writer',
                'description': 'x',
                'defer_start': True,
            },
        )
        assert r.status_code == 200, r.text
        task_id = r.json()['id']
        asyncio.create_task(_background_writer(override_async_session, task_id))
        await real_sleep(0.05)

    async def test_the_drain_finishes_the_leaked_writers_writes(
        self, admin_client, install_fake_agent, override_async_session
    ):
        """The drain lets in-flight work FINISH — it never cancels a
        writer in the middle of a database call.

        This is the drain's actual contract (see
        ``drain_background_tasks``): the teardown's safety comes from
        waiting for what is mid-write and only then stopping what is
        parked. Asserted here, mid-test, because the teardown variant
        can only be observed through a side channel — and a
        per-test database file no longer dies when a call is cut
        short, so "the teardown did not error" no longer tells the
        two designs apart. The writer's write landing does.
        """
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Writer finished by the drain',
                'description': 'x',
                'defer_start': True,
            },
        )
        assert r.status_code == 200, r.text
        task_id = r.json()['id']
        writer = asyncio.create_task(
            _background_writer(override_async_session, task_id)
        )
        await real_sleep(0.05)
        assert not writer.done()

        await drain_background_tasks()

        assert writer.done(), 'the drain cancelled the writer mid-work'
        async with override_async_session() as db:
            task = await db.get(Task, task_id)
        assert task is not None
        assert task.transcript_tail == 'written after the test returned'
