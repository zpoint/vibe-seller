"""The browser/start route surfaces the vendor's InjectError taxonomy.

W5-3: a wenmai backend failure (the stale-kernel case) reads as a
structured status (status/message/hint), not a raw 500 — C2's typed
body and the docs link reach the user.
"""

import json
from unittest import mock

import pytest

from app.browser.inject.errors import CdpUnreachable
from app.models.store import Store
import app.routers.stores as stores_mod
import app.routers.tasks as tasks_mod

pytestmark = pytest.mark.workflow


class _TypedBackend:
    """A minimal backend whose start() replays the client's error."""

    def __init__(self, client):
        self._client = client

    async def start(self, browser_config: dict):
        await self._client.ensure_running()
        await self._client.ensure_logged_in()
        await self._client.open_store(browser_config.get('browser_ref', ''))
        raise AssertionError('should not reach here')

    async def stop(self, info):
        return None


async def _add_wenmai_store(name='wm-typed-store'):
    async with tasks_mod.async_session() as db:
        store = Store(
            name=name,
            browser_backend='wenmai',
            browser_oauth='100000000001',
        )
        db.add(store)
        await db.commit()
        await db.refresh(store)
        return store.id


async def test_browser_start_surfaces_inject_errors_typed(admin_client):
    """The wedged-kernel recovery refusing a safe kill raised
    CdpUnreachable through this route and read as 500 before; it must
    surface as the typed cdp_unreachable status with the hint."""
    store_id = await _add_wenmai_store()
    # The conftest's admin_client fixture mocks browser_manager in the
    # stores router; make the MOCK raise the InjectError.
    stores_mod.browser_manager.start_session = mock.AsyncMock(
        side_effect=CdpUnreachable(
            'stale kernel could not be identified for a safe kill'
        )
    )
    r = await admin_client.post(f'/api/stores/{store_id}/browser/start')
    assert r.status_code == 502
    detail = json.loads(r.json()['detail'])
    assert detail['status'] == 'cdp_unreachable'
    assert 'stale kernel' in detail['message']
    assert detail['hint']  # the operator hint rides along
