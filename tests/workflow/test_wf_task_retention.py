"""Workflow tests for task_retention_days settings CRUD.

Mirrors tests/workflow/test_wf_concurrency.py — the new setting
goes through the same `/api/settings` GET/PUT pipeline.
"""

import pytest

from app.models.app_settings import AppSettings

pytestmark = pytest.mark.workflow


class TestTaskRetentionSettings:
    async def test_get_returns_default(self, admin_client):
        """Default surfaces as 30 when no DB row exists."""
        r = await admin_client.get('/api/settings')
        assert r.status_code == 200
        data = r.json()
        assert data.get('task_retention_days') == '30'

    async def test_put_persists_value(
        self, admin_client, override_async_session
    ):
        r = await admin_client.put(
            '/api/settings', json={'task_retention_days': 60}
        )
        assert r.status_code == 200
        async with override_async_session() as db:
            row = await db.get(AppSettings, 'task_retention_days')
            assert row is not None and row.value == '60'

        r = await admin_client.get('/api/settings')
        assert r.json()['task_retention_days'] == '60'

    async def test_put_zero_is_allowed_disables_cleanup(
        self, admin_client, override_async_session
    ):
        """0 is the documented "disable" value — must persist as-is."""
        r = await admin_client.put(
            '/api/settings', json={'task_retention_days': 0}
        )
        assert r.status_code == 200
        async with override_async_session() as db:
            row = await db.get(AppSettings, 'task_retention_days')
            assert row is not None and row.value == '0'

    async def test_put_clamps_above_max(
        self, admin_client, override_async_session
    ):
        await admin_client.put(
            '/api/settings', json={'task_retention_days': 99999}
        )
        async with override_async_session() as db:
            row = await db.get(AppSettings, 'task_retention_days')
            assert row is not None
            assert int(row.value) == 3650

    async def test_put_clamps_negative_to_zero(
        self, admin_client, override_async_session
    ):
        await admin_client.put(
            '/api/settings', json={'task_retention_days': -10}
        )
        async with override_async_session() as db:
            row = await db.get(AppSettings, 'task_retention_days')
            assert row is not None and row.value == '0'

    async def test_put_ignores_invalid(
        self, admin_client, override_async_session
    ):
        await admin_client.put(
            '/api/settings', json={'task_retention_days': 'forever'}
        )
        async with override_async_session() as db:
            row = await db.get(AppSettings, 'task_retention_days')
            assert row is None  # no value committed for invalid input

    async def test_put_requires_admin(self, member_client):
        r = await member_client.put(
            '/api/settings', json={'task_retention_days': 7}
        )
        assert r.status_code == 403
