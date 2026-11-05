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
from tests.workflow.conftest import real_sleep

pytestmark = pytest.mark.workflow


async def _background_writer(session_maker, task_id: str) -> None:
    """A writer that holds a database session while it works.

    This is what the app's own pipelines do (auto_run_task, the
    follow-up spawn): they run off the request path and write to the
    database while the test that launched them is already over.
    """
    async with session_maker() as db:
        # A long query: the connection is BUSY inside the call when
        # the teardown cancels this task — which is what invalidates
        # it (SQLAlchemy discards a connection whose call was cut
        # short) and closes the database under the fixture.
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
