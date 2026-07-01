"""Router-level tests for app.routers.system — dev-gate, platform
branching, and the in-process update-check cache.

Calls the route function directly (no auth dependency on this router,
so no TestClient/app fixture needed) with fetch_latest_pypi_version /
fetch_release_notes / get_platform monkeypatched.
"""

from unittest.mock import AsyncMock

import pytest

import app.routers.system as system_router

pytestmark = pytest.mark.unit

_SAMPLE_RELEASES = [
    {
        'version': '0.7.0',
        'name': 'v0.7.0',
        'body': 'Adds thing.',
        'url': 'https://x/0.7.0',
        'published_at': '2026-06-01T00:00:00Z',
    }
]


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test gets a cold cache — it's module-global state."""
    system_router._update_check_cache = None
    system_router._update_check_cache_at = 0.0
    yield
    system_router._update_check_cache = None
    system_router._update_check_cache_at = 0.0


class TestUpdateCheckRoute:
    async def test_dev_version_short_circuits(self, monkeypatch):
        monkeypatch.setattr(system_router, 'APP_VERSION', '0.1.0.dev3+gabc123')
        assert await system_router.update_check() == {'dev': True}

    async def test_up_to_date_returns_not_available(self, monkeypatch):
        monkeypatch.setattr(system_router, 'APP_VERSION', '0.6.0')
        monkeypatch.setattr(
            system_router,
            'fetch_latest_pypi_version',
            AsyncMock(return_value='0.6.0'),
        )
        result = await system_router.update_check()
        assert result == {
            'dev': False,
            'update_available': False,
            'current_version': '0.6.0',
        }

    async def test_pypi_unreachable_returns_not_available(self, monkeypatch):
        monkeypatch.setattr(system_router, 'APP_VERSION', '0.6.0')
        monkeypatch.setattr(
            system_router,
            'fetch_latest_pypi_version',
            AsyncMock(return_value=None),
        )
        result = await system_router.update_check()
        assert result['update_available'] is False

    async def test_update_available_non_windows_uses_cli_command(
        self, monkeypatch
    ):
        monkeypatch.setattr(system_router, 'APP_VERSION', '0.6.0')
        monkeypatch.setattr(
            system_router,
            'fetch_latest_pypi_version',
            AsyncMock(return_value='0.7.0'),
        )
        monkeypatch.setattr(
            system_router,
            'fetch_release_notes',
            AsyncMock(return_value=_SAMPLE_RELEASES),
        )
        monkeypatch.setattr(system_router, 'get_platform', lambda: 'mac')

        result = await system_router.update_check()

        assert result['update_available'] is True
        assert result['latest_version'] == '0.7.0'
        assert result['upgrade_command'] == 'vibe-seller upgrade'
        assert result['download_url'] is None
        assert result['releases_page_url'] == system_router.RELEASES_PAGE_URL
        assert result['releases'] == _SAMPLE_RELEASES

    async def test_update_available_windows_uses_download_link(
        self, monkeypatch
    ):
        monkeypatch.setattr(system_router, 'APP_VERSION', '0.6.0')
        monkeypatch.setattr(
            system_router,
            'fetch_latest_pypi_version',
            AsyncMock(return_value='0.7.0'),
        )
        monkeypatch.setattr(
            system_router, 'fetch_release_notes', AsyncMock(return_value=[])
        )
        monkeypatch.setattr(system_router, 'get_platform', lambda: 'windows')

        result = await system_router.update_check()

        assert result['upgrade_command'] is None
        assert result['download_url'] == system_router.RELEASES_PAGE_URL


class TestUpdateCheckCache:
    async def test_second_call_within_ttl_skips_network(self, monkeypatch):
        monkeypatch.setattr(system_router, 'APP_VERSION', '0.6.0')
        pypi_mock = AsyncMock(return_value='0.7.0')
        monkeypatch.setattr(
            system_router, 'fetch_latest_pypi_version', pypi_mock
        )
        monkeypatch.setattr(
            system_router, 'fetch_release_notes', AsyncMock(return_value=[])
        )
        monkeypatch.setattr(system_router, 'get_platform', lambda: 'mac')

        first = await system_router.update_check()
        second = await system_router.update_check()

        assert first == second
        pypi_mock.assert_awaited_once()

    async def test_call_after_ttl_expiry_refetches(self, monkeypatch):
        monkeypatch.setattr(system_router, 'APP_VERSION', '0.6.0')
        pypi_mock = AsyncMock(return_value='0.7.0')
        monkeypatch.setattr(
            system_router, 'fetch_latest_pypi_version', pypi_mock
        )
        monkeypatch.setattr(
            system_router, 'fetch_release_notes', AsyncMock(return_value=[])
        )
        monkeypatch.setattr(system_router, 'get_platform', lambda: 'mac')

        clock = [0.0]
        monkeypatch.setattr(system_router.time, 'monotonic', lambda: clock[0])

        await system_router.update_check()
        clock[0] = system_router._UPDATE_CHECK_CACHE_TTL_SECONDS + 1
        await system_router.update_check()

        assert pypi_mock.await_count == 2

    async def test_failed_check_is_also_cached(self, monkeypatch):
        monkeypatch.setattr(system_router, 'APP_VERSION', '0.6.0')
        pypi_mock = AsyncMock(return_value=None)
        monkeypatch.setattr(
            system_router, 'fetch_latest_pypi_version', pypi_mock
        )

        await system_router.update_check()
        await system_router.update_check()

        pypi_mock.assert_awaited_once()
