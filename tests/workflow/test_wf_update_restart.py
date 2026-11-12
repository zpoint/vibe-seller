"""Workflow tests for the in-app update endpoints.

``POST /api/system/update/restart`` is the money endpoint: admin only,
409 unless a download is actually staged, and on success it hands the
restart to ``app/auto_update.py``. The apply paths themselves are
unit-tested; here the HTTP contract + auth gate are pinned.
"""

import asyncio
from contextlib import suppress

import pytest

import app.auto_update as au

pytestmark = pytest.mark.workflow


@pytest.fixture(autouse=True)
def _reset_state():
    au.reset_for_tests()
    yield
    au.reset_for_tests()


class TestUpdateStatus:
    async def test_reports_idle_on_a_test_machine(self, member_client):
        r = await member_client.get('/api/system/update-status')
        assert r.status_code == 200
        body = r.json()
        # Under pytest the kill switch defaults off → not supported.
        assert body['supported'] is False
        assert body['state'] == 'idle'
        assert body['running_tasks'] == 0

    async def test_ready_stage_is_visible_to_any_logged_in_user(
        self, member_client, monkeypatch
    ):
        au._update(state=au.READY, version='9.9.9')
        r = await member_client.get('/api/system/update-status')
        body = r.json()
        assert body['state'] == 'ready'
        assert body['version'] == '9.9.9'

    async def test_requires_login(self, unauthed_client):
        r = await unauthed_client.get('/api/system/update-status')
        assert r.status_code == 401


class TestRestartGate:
    async def test_member_gets_403(self, member_client):
        r = await member_client.post('/api/system/update/restart')
        assert r.status_code == 403

    async def test_admin_409_when_not_ready(self, admin_client):
        assert au.snapshot()['state'] == 'idle'
        r = await admin_client.post('/api/system/update/restart')
        assert r.status_code == 409

    async def test_admin_409_while_downloading(self, admin_client):
        au._update(state=au.DOWNLOADING, version='9.9.9')
        r = await admin_client.post('/api/system/update/restart')
        assert r.status_code == 409

    async def test_admin_ok_when_ready(self, admin_client, monkeypatch):
        # The unix apply path spawns `vibe-seller upgrade` — replace
        # only the spawn, keep the endpoint + state machine for real.
        calls: list = []

        class _Proc:
            returncode = None

            def poll(self):
                return None

        def fake_spawn():
            calls.append(1)
            return _Proc()

        monkeypatch.setattr(au, '_spawn_unix_upgrade', fake_spawn)
        monkeypatch.setattr(
            au.upgrade_restart,
            'installed_version',
            lambda: au.get_version(),
        )
        au._update(state=au.READY, version='9.9.9')
        r = await admin_client.post('/api/system/update/restart')
        assert r.status_code == 200
        assert r.json()['state'] == 'restarting'
        assert r.json()['version'] == '9.9.9'
        assert calls == [1]
        assert au.snapshot()['state'] == au.RESTARTING
        # The watcher would poll until its timeout — stop it.
        if au._apply_task is not None:
            au._apply_task.cancel()
            with suppress(asyncio.CancelledError):
                await au._apply_task
