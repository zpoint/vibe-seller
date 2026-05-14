"""Integration tests for browser session lifecycle."""

from unittest import mock

import pytest

from app.browser.base import BrowserSessionInfo
from app.browser.manager import BrowserManager


class TestBrowserLifecycle:
    """Tests for browser session lifecycle."""

    @pytest.fixture
    def manager(self):
        """Create a browser manager instance."""
        return BrowserManager()

    @pytest.mark.asyncio
    async def test_manager_has_browser_method(self, manager):
        """Test that manager has get_browser method."""
        # Initially no browser
        browser = manager.get_browser('test-store')
        assert browser is None

    @pytest.mark.asyncio
    async def test_manager_tracks_sessions(
        self, manager, test_store, async_db_session
    ):
        """Test that manager tracks active sessions."""
        # Mock the backend with proper return values
        mock_backend = mock.AsyncMock()
        mock_session_info = mock.MagicMock(spec=BrowserSessionInfo)
        mock_session_info.cdp_port = 9222
        mock_session_info.pid = 12345
        mock_session_info.browser = mock.MagicMock()
        mock_backend.start.return_value = mock_session_info

        def fake_get_backend(store_id, backend_type):
            manager._backends[store_id] = mock_backend
            return mock_backend

        with mock.patch.object(
            manager, '_get_backend', side_effect=fake_get_backend
        ):
            # Start session
            session = await manager.start_session(test_store, async_db_session)

            assert session is not None
            assert session.store_id == test_store.id
            assert session.status == 'running'
            mock_backend.start.assert_called_once()

            # Stop session
            await manager.stop_session(test_store, async_db_session)
            mock_backend.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_browser_returns_browser(
        self, manager, test_store, async_db_session
    ):
        """Test get_browser returns browser object."""
        mock_backend = mock.AsyncMock()
        mock_browser = mock.MagicMock()
        mock_session_info = mock.MagicMock(spec=BrowserSessionInfo)
        mock_session_info.browser = mock_browser
        mock_session_info.cdp_port = 9222
        mock_session_info.pid = 12345
        mock_backend.start.return_value = mock_session_info

        with mock.patch.object(
            manager, '_get_backend', return_value=mock_backend
        ):
            # Start session
            await manager.start_session(test_store, async_db_session)

            # Get browser
            browser = manager.get_browser(test_store.id)
            assert browser == mock_browser

            # Cleanup
            await manager.stop_session(test_store, async_db_session)

    @pytest.mark.asyncio
    async def test_cdp_port_tracking(
        self, manager, test_store, async_db_session
    ):
        """Test CDP port tracking."""
        mock_backend = mock.AsyncMock()
        mock_session_info = mock.MagicMock(spec=BrowserSessionInfo)
        mock_session_info.cdp_port = 9222
        mock_session_info.pid = 12345
        mock_session_info.browser = mock.MagicMock()
        mock_backend.start.return_value = mock_session_info

        with mock.patch.object(
            manager, '_get_backend', return_value=mock_backend
        ):
            # Start session
            await manager.start_session(test_store, async_db_session)

            # Get CDP port
            port = manager.get_cdp_port(test_store.id)
            assert port == 9222

            # Cleanup
            await manager.stop_session(test_store, async_db_session)
