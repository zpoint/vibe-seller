"""Tests for stores router."""

import asyncio
import inspect
import json
from unittest import mock
import uuid

from httpx import AsyncClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.launch_guards import BrowserBusyError
from app.models.store import Store
from app.models.user import User
from app.models.ziniao_account import ZiniaoAccount
from app.utils.crypto import encrypt_password
from tests.unit.test_browser.ziniao_fake_host import (
    MAIN_EXE,
    NORMAL_MODE,
    FakeZiniaoHost,
    installed,
)


class TestStoresRouter:
    """Tests for /api/stores endpoints."""

    @pytest.mark.asyncio
    async def test_create_store(self, authenticated_client: AsyncClient):
        """Test POST /api/stores creates a store."""
        store_data = {
            'name': 'Test Store',
            'browser_backend': 'chrome',
            'browser_config': {'headless': True},
            'platforms': ['amazon'],
            'countries': ['US'],
        }

        with mock.patch('app.routers.stores.workspace_manager') as mock_ws:
            mock_ws.root = mock.MagicMock()
            mock_ws.root.__truediv__ = mock.MagicMock(
                return_value=mock.MagicMock()
            )

            response = await authenticated_client.post(
                '/api/stores', json=store_data
            )

        assert response.status_code == 200
        data = response.json()
        assert data['name'] == 'Test Store'
        assert data['browser_backend'] == 'chrome'
        assert data['browser_config'] == {'headless': True}
        assert data['platforms'] == ['amazon']
        assert data['countries'] == ['US']
        assert 'id' in data

    @pytest.mark.asyncio
    async def test_list_stores(
        self, authenticated_client: AsyncClient, test_store: Store
    ):
        """Test GET /api/stores lists stores."""
        response = await authenticated_client.get('/api/stores')

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        # Find our test store in the list
        store_ids = [s['id'] for s in data]
        assert test_store.id in store_ids

    @pytest.mark.asyncio
    async def test_get_store(
        self, authenticated_client: AsyncClient, test_store: Store
    ):
        """Test GET /api/stores/{id} returns store."""
        response = await authenticated_client.get(
            f'/api/stores/{test_store.id}'
        )

        assert response.status_code == 200
        data = response.json()
        assert data['id'] == test_store.id
        assert data['name'] == test_store.name
        assert data['browser_backend'] == test_store.browser_backend

    @pytest.mark.asyncio
    async def test_get_store_not_found(self, authenticated_client: AsyncClient):
        """Test GET /api/stores/{id} returns 404 for non-existent store."""
        response = await authenticated_client.get('/api/stores/non-existent-id')

        assert response.status_code == 404
        assert response.json()['detail'] == 'Store not found'

    @pytest.mark.asyncio
    async def test_delete_store(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
        test_user: User,
    ):
        """Test DELETE /api/stores/{id} deletes store."""
        # Create a store to delete

        store = Store(
            name='Delete Me',
            browser_backend='chrome',
            browser_config=json.dumps({}),
        )
        async_db_session.add(store)
        await async_db_session.commit()

        # Delete it
        response = await authenticated_client.delete(f'/api/stores/{store.id}')
        assert response.status_code == 200
        assert response.json() == {'ok': True}

        # Verify it's gone
        get_response = await authenticated_client.get(f'/api/stores/{store.id}')
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_create_store_requires_auth(self, async_client: AsyncClient):
        """Test store creation requires authentication."""
        store_data = {
            'name': 'Test Store',
            'browser_backend': 'chrome',
            'browser_config': {},
        }

        response = await async_client.post('/api/stores', json=store_data)

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_list_stores_requires_auth(self, async_client: AsyncClient):
        """Test listing stores requires authentication."""
        response = await async_client.get('/api/stores')

        assert response.status_code == 401


class TestBrowserStart:
    """POST /api/stores/{id}/browser/start — Ziniao always comes up.

    The user's spec: "all cases mean force=true" — every automatic path
    relaunches Ziniao instead of erroring, and the ``force`` switch is
    gone. A Ziniao found in normal mode is killed and relaunched in
    WebDriver mode by the browser start itself.
    """

    async def _make_ziniao_store(self, async_db_session: AsyncSession) -> Store:
        account = ZiniaoAccount(
            id=str(uuid.uuid4()),
            name='test-account',
            company='c',
            username='u',
            encrypted_password=encrypt_password('p'),
            socket_port=16851,
            client_path=MAIN_EXE,
        )
        async_db_session.add(account)
        store = Store(
            id=str(uuid.uuid4()),
            name='Z Store',
            browser_backend='ziniao',
            browser_config='{}',
            ziniao_account_id=account.id,
            platforms='["amazon"]',
            countries='["US"]',
        )
        async_db_session.add(store)
        await async_db_session.commit()
        return store

    def test_the_route_has_no_force_switch(self):
        from app.routers.stores import start_browser  # noqa: PLC0415

        assert 'force' not in inspect.signature(start_browser).parameters

    @pytest.mark.asyncio
    async def test_normal_mode_is_relaunched_with_no_flag(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
    ):
        """Ziniao opened by hand: the plain start kills it, relaunches it in
        WebDriver mode and starts the store — 200, no error, no prompt."""
        store = await self._make_ziniao_store(async_db_session)
        host = FakeZiniaoHost(api='down', procs=NORMAL_MODE)
        with (
            installed(host),
            mock.patch(
                'app.browser.manager.BrowserManager._start_session_locked',
                new=mock.AsyncMock(return_value=None),
            ),
        ):
            response = await authenticated_client.post(
                f'/api/stores/{store.id}/browser/start'
            )
        assert response.status_code == 200, response.text
        assert len(host.launches) == 1
        assert sorted(n for n, _ in host.killed) == sorted(
            n for n, _ in NORMAL_MODE
        )

    @pytest.mark.asyncio
    async def test_start_returns_typed_proxy_port_and_instance_id(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
    ):
        """PR C1 contract: the success body carries the proxy port and
        the server's instance id, so a wrapper that triggered the start
        can follow a REALLOCATED port instead of attaching to whatever
        listener holds its baked one."""
        from app.browser.manager import browser_manager  # noqa: PLC0415

        store = await self._make_ziniao_store(async_db_session)
        session = mock.Mock(proxy_port=9227)
        with mock.patch(
            'app.routers.stores.browser_manager.start_session',
            new=mock.AsyncMock(return_value=session),
        ):
            response = await authenticated_client.post(
                f'/api/stores/{store.id}/browser/start'
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['ok'] is True
        assert data['proxy_port'] == 9227
        assert data['instance_id'] == browser_manager.instance_id

    @pytest.mark.asyncio
    async def test_an_old_wrapper_sending_force_still_works(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
    ):
        """Wrappers written before this change send ``?force=1``; the
        parameter is gone and ignored, the start still succeeds."""
        store = await self._make_ziniao_store(async_db_session)
        with mock.patch(
            'app.routers.stores.browser_manager.start_session',
            new=mock.AsyncMock(return_value=None),
        ):
            response = await authenticated_client.post(
                f'/api/stores/{store.id}/browser/start?force=1'
            )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_a_busy_launch_lock_is_503_not_a_hang(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
    ):
        """The HTTP contract this route regressed to `HTTP 000` without.

        A wedged holder of the manager's global launch lock used to make
        this handler wait for ever, so the caller got no response at all
        — 240s and `HTTP 000`, which names nothing to restart. 503
        rather than 500 because nothing is known to be wrong with THIS
        store, and the detail must carry the holder so an operator can
        tell contention from a stuck client.
        """
        store = await self._make_ziniao_store(async_db_session)
        with mock.patch(
            'app.routers.stores.browser_manager.start_session',
            side_effect=BrowserBusyError(
                'Browser subsystem busy: start_session(Z Store) waited '
                '60s for the launch lock, which is held by '
                'start_session(other).'
            ),
        ):
            response = await authenticated_client.post(
                f'/api/stores/{store.id}/browser/start'
            )
        assert response.status_code == 503
        assert 'held by start_session(other)' in response.json()['detail']

    @pytest.mark.asyncio
    async def test_a_busy_lock_is_503_and_starts_nothing_else(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
    ):
        """A busy lock is not normal mode: one 503, no second start that
        could relaunch Ziniao because somebody else was mid-launch — the
        storm the relaunch budget exists to stop."""
        store = await self._make_ziniao_store(async_db_session)
        start = mock.AsyncMock(side_effect=BrowserBusyError('busy: peer'))
        with mock.patch(
            'app.routers.stores.browser_manager.start_session', start
        ):
            response = await authenticated_client.post(
                f'/api/stores/{store.id}/browser/start?force=1'
            )
        assert response.status_code == 503
        assert start.await_count == 1

    @pytest.mark.asyncio
    async def test_ziniao_still_starting_is_503(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
    ):
        from app.browser.ziniao_startup import (  # noqa: PLC0415
            ZiniaoStartingError,
        )

        store = await self._make_ziniao_store(async_db_session)
        with mock.patch(
            'app.routers.stores.browser_manager.start_session',
            side_effect=ZiniaoStartingError('Ziniao is starting (61s)', 61),
        ):
            response = await authenticated_client.post(
                f'/api/stores/{store.id}/browser/start'
            )
        assert response.status_code == 503
        assert 'starting' in response.json()['detail']

    @pytest.mark.asyncio
    async def test_another_account_in_use_is_409(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
    ):
        from app.browser.ziniao_startup import (  # noqa: PLC0415
            ZiniaoAccountConflictError,
        )

        store = await self._make_ziniao_store(async_db_session)
        with mock.patch(
            'app.routers.stores.browser_manager.start_session',
            side_effect=ZiniaoAccountConflictError('another account'),
        ):
            response = await authenticated_client.post(
                f'/api/stores/{store.id}/browser/start'
            )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_a_failed_ziniao_start_is_500_with_the_reason(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
    ):
        from app.browser.ziniao_startup import (  # noqa: PLC0415
            ZiniaoStartError,
        )

        store = await self._make_ziniao_store(async_db_session)
        with mock.patch(
            'app.routers.stores.browser_manager.start_session',
            side_effect=ZiniaoStartError(
                'Ziniao did not start: opened 2 times',
                'startup_timeout',
            ),
        ):
            response = await authenticated_client.post(
                f'/api/stores/{store.id}/browser/start'
            )
        assert response.status_code == 500
        assert 'opened 2 times' in response.json()['detail']

    @pytest.mark.asyncio
    async def test_simultaneous_starts_share_one_kill_and_one_launch(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
    ):
        """Three Ziniao stores' tasks reach the browser at the same moment
        while Ziniao sits in normal mode: ONE kill-all and ONE launch, and
        every start succeeds. A second kill-all would kill the first one's
        freshly launched client."""
        first = await self._make_ziniao_store(async_db_session)
        account_id = first.ziniao_account_id
        stores = [first]
        for name in ('Z Store 2', 'Z Store 3'):
            store = Store(
                id=str(uuid.uuid4()),
                name=name,
                browser_backend='ziniao',
                browser_config='{}',
                ziniao_account_id=account_id,
                platforms='["amazon"]',
                countries='["US"]',
            )
            async_db_session.add(store)
            stores.append(store)
        await async_db_session.commit()
        host = FakeZiniaoHost(api='down', procs=NORMAL_MODE)
        with (
            installed(host),
            mock.patch(
                'app.browser.manager.BrowserManager._start_session_locked',
                new=mock.AsyncMock(return_value=None),
            ),
        ):
            responses = await asyncio.gather(
                *(
                    authenticated_client.post(
                        f'/api/stores/{s.id}/browser/start'
                    )
                    for s in stores
                )
            )
        assert [r.status_code for r in responses] == [200, 200, 200]
        assert len(host.launches) == 1
        assert sorted(n for n, _ in host.killed) == sorted(
            n for n, _ in NORMAL_MODE
        )
