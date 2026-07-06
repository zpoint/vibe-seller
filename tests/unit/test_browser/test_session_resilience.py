"""Tests for browser session resilience.

Covers all failure modes where Ziniao/CDP state gets out of sync:
1. Ziniao killed externally (process dead, in-memory + DB say "running")
2. Server restart (in-memory empty, DB says "running")
3. backend.stop() raises during cleanup (crashed Ziniao)
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import socket
import threading
from unittest import mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from app.browser.base import BrowserSessionInfo
from app.browser.manager import BrowserManager
from app.browser.ziniao_utils import ZiniaoNormalModeError
from app.models.base import Base
from app.models.browser_session import BrowserSession
from app.models.store import Store

# ── Helpers ──────────────────────────────────────────


def _unused_port() -> int:
    """Return a port that is definitely not in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class _OkHandler(BaseHTTPRequestHandler):
    """Returns 200 for /json/version."""

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'{}')

    def log_message(self, *a):
        pass


def _make_fake_store(store_id='s1', name='test-store'):
    store = mock.MagicMock()
    store.id = store_id
    store.name = name
    store.browser_backend = 'ziniao'
    store.browser_config = '{}'
    store.ziniao_account_id = None
    store.browser_oauth = None
    store.platform_countries = '{}'
    return store


def _make_fake_session(store_id='s1', status='running'):
    session = mock.MagicMock()
    session.store_id = store_id
    session.status = status
    session.proxy_port = 9222
    session.cdp_port = 31929
    session.chrome_pid = 12345
    return session


# ── _cdp_alive helper tests ──────────────────────────


@pytest.mark.unit
class TestCdpAlive:
    """BrowserManager._cdp_alive detects live vs dead ports."""

    @pytest.mark.asyncio
    async def test_false_for_dead_port(self):
        """Port with nothing listening → False."""
        port = _unused_port()
        assert await BrowserManager._cdp_alive(port) is False

    @pytest.mark.asyncio
    async def test_true_for_live_port(self):
        """HTTP server returning 200 → True."""
        server = HTTPServer(('127.0.0.1', 0), _OkHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            assert await BrowserManager._cdp_alive(port) is True
        finally:
            server.shutdown()


# ── wrapper-safety: never leave the agent wrapper-less ──


@pytest.mark.unit
class TestWrapperWrittenBeforeStart:
    """The per-store browser-use wrapper must exist even when the browser
    fails to start. If a stale/failed launch left NO wrapper, the agent's
    bare ``browser-use`` falls through PATH to the REAL binary and attaches
    to a LOCAL Chrome (the user's own browser). Regression guard — see
    docs/ziniao-concurrency.md."""

    @pytest.mark.asyncio
    async def test_wrapper_written_even_when_start_fails(self):
        mgr = BrowserManager()
        store = _make_fake_store()  # no active session → fresh-start path

        fake_db = mock.AsyncMock()
        fake_db.execute = mock.AsyncMock(
            return_value=mock.MagicMock(
                scalar_one_or_none=mock.MagicMock(return_value=None)
            )
        )
        fake_db.get = mock.AsyncMock(return_value=None)

        failing_backend = mock.AsyncMock()
        failing_backend.start = mock.AsyncMock(
            side_effect=RuntimeError('stale launch: nothing reachable')
        )

        with (
            mock.patch.object(
                mgr, '_get_backend', return_value=failing_backend
            ),
            mock.patch('app.browser.manager.write_browser_use_wrapper') as wrap,
            mock.patch('app.browser.manager.create_token', return_value='tok'),
        ):
            with pytest.raises(RuntimeError, match='stale launch'):
                await mgr._start_session_locked(store, fake_db)

        # Wrapper written despite the start failure → the agent's
        # `browser-use` still resolves to the safe wrapper, never a
        # local Chrome.
        wrap.assert_called_once()


# ── start_session resilience tests ───────────────────


@pytest.mark.unit
class TestStartSessionDetectsDeadCdp:
    """Failure mode 1: Ziniao killed, in-memory + DB say 'running'."""

    @pytest.mark.asyncio
    async def test_dead_cdp_triggers_restart(self):
        """Session 'running' but CDP dead → full restart."""
        mgr = BrowserManager()
        store = _make_fake_store()
        dead_port = _unused_port()

        mgr._active_sessions[store.id] = BrowserSessionInfo()
        mgr._proxy_ports[store.id] = dead_port

        fake_session = _make_fake_session()
        fake_db = mock.AsyncMock()
        fake_db.execute = mock.AsyncMock(
            return_value=mock.MagicMock(
                scalar_one_or_none=mock.MagicMock(return_value=fake_session)
            )
        )
        fake_db.get = mock.AsyncMock(return_value=None)

        fake_backend = mock.AsyncMock()
        fake_backend.start = mock.AsyncMock(
            return_value=BrowserSessionInfo(cdp_port=55555)
        )
        mgr._backends[store.id] = fake_backend

        new_backend = mock.AsyncMock()
        new_backend.start = mock.AsyncMock(
            return_value=BrowserSessionInfo(cdp_port=55555)
        )

        with (
            mock.patch.object(mgr, '_get_backend', return_value=new_backend),
            mock.patch('app.browser.manager.write_browser_use_wrapper'),
            mock.patch(
                'app.browser.manager.create_token',
                return_value='tok',
            ),
        ):
            await mgr._start_session_locked(store, fake_db)

        fake_backend.stop.assert_called_once()
        new_backend.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_live_cdp_reuses_session(self):
        """Session 'running' and CDP alive → no restart."""
        mgr = BrowserManager()
        store = _make_fake_store()

        server = HTTPServer(('127.0.0.1', 0), _OkHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        mgr._active_sessions[store.id] = BrowserSessionInfo()
        mgr._proxy_ports[store.id] = port

        fake_session = _make_fake_session()
        fake_db = mock.AsyncMock()
        fake_db.execute = mock.AsyncMock(
            return_value=mock.MagicMock(
                scalar_one_or_none=mock.MagicMock(return_value=fake_session)
            )
        )

        with (
            mock.patch('app.browser.manager.write_browser_use_wrapper'),
            mock.patch(
                'app.browser.manager.create_token',
                return_value='tok',
            ),
        ):
            result = await mgr._start_session_locked(store, fake_db)

        server.shutdown()
        assert result is fake_session


@pytest.mark.unit
class TestStartSessionStaleDbNoMemory:
    """Failure mode 2: Server restart — DB 'running', memory empty."""

    @pytest.mark.asyncio
    async def test_stale_db_does_fresh_start(self):
        """Session 'running' in DB but not in _active_sessions."""
        mgr = BrowserManager()
        store = _make_fake_store()

        fake_session = _make_fake_session()
        fake_db = mock.AsyncMock()
        fake_db.execute = mock.AsyncMock(
            return_value=mock.MagicMock(
                scalar_one_or_none=mock.MagicMock(return_value=fake_session)
            )
        )
        fake_db.get = mock.AsyncMock(return_value=None)
        fake_db.commit = mock.AsyncMock()

        new_backend = mock.AsyncMock()
        new_backend.start = mock.AsyncMock(
            return_value=BrowserSessionInfo(cdp_port=55555)
        )

        with (
            mock.patch.object(mgr, '_get_backend', return_value=new_backend),
            mock.patch('app.browser.manager.write_browser_use_wrapper'),
            mock.patch(
                'app.browser.manager.create_token',
                return_value='tok',
            ),
        ):
            await mgr._start_session_locked(store, fake_db)

        new_backend.start.assert_called_once()


@pytest.mark.unit
class TestDeadCdpCleanupToleratesStopError:
    """backend.stop() raises during cleanup → still restarts."""

    @pytest.mark.asyncio
    async def test_stop_error_does_not_derail_restart(self):
        mgr = BrowserManager()
        store = _make_fake_store()
        dead_port = _unused_port()

        mgr._active_sessions[store.id] = BrowserSessionInfo()
        mgr._proxy_ports[store.id] = dead_port

        bad_backend = mock.AsyncMock()
        bad_backend.stop = mock.AsyncMock(
            side_effect=RuntimeError('proxy already dead')
        )
        mgr._backends[store.id] = bad_backend

        fake_session = _make_fake_session()
        fake_db = mock.AsyncMock()
        fake_db.execute = mock.AsyncMock(
            return_value=mock.MagicMock(
                scalar_one_or_none=mock.MagicMock(return_value=fake_session)
            )
        )
        fake_db.get = mock.AsyncMock(return_value=None)
        fake_db.commit = mock.AsyncMock()

        new_backend = mock.AsyncMock()
        new_backend.start = mock.AsyncMock(
            return_value=BrowserSessionInfo(cdp_port=55555)
        )

        with (
            mock.patch.object(mgr, '_get_backend', return_value=new_backend),
            mock.patch('app.browser.manager.write_browser_use_wrapper'),
            mock.patch(
                'app.browser.manager.create_token',
                return_value='tok',
            ),
        ):
            await mgr._start_session_locked(store, fake_db)

        bad_backend.stop.assert_called_once()
        new_backend.start.assert_called_once()


# ── cleanup_stale_sessions tests ─────────────────────


@pytest.mark.unit
class TestCleanupStaleSessions:
    """Server startup marks stale 'running' sessions as idle."""

    @pytest.mark.asyncio
    async def test_marks_running_as_idle(self):
        """Two running sessions → both marked idle."""
        # Use isolated in-memory DB
        mem_engine = create_async_engine('sqlite+aiosqlite://', echo=False)
        mem_session = async_sessionmaker(mem_engine, expire_on_commit=False)

        async with mem_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Insert stores + sessions via ORM (respects defaults)
        async with mem_session() as db:
            db.add(Store(id='s1', name='st1', browser_backend='ziniao'))
            db.add(Store(id='s2', name='st2', browser_backend='ziniao'))
            db.add(
                BrowserSession(
                    store_id='s1',
                    status='running',
                    cdp_port=9222,
                    proxy_port=9222,
                )
            )
            db.add(
                BrowserSession(
                    store_id='s2',
                    status='running',
                    cdp_port=9223,
                    proxy_port=9223,
                )
            )
            await db.commit()

        mgr = BrowserManager()
        with mock.patch('app.browser.manager.async_session', mem_session):
            count = await mgr.cleanup_stale_sessions()

        assert count == 2

        # Verify they're idle
        async with mem_session() as db:
            result = await db.execute(
                select(BrowserSession).where(
                    BrowserSession.store_id.in_(['s1', 's2'])
                )
            )
            for s in result.scalars().all():
                assert s.status == 'idle'
                assert s.cdp_port is None
                assert s.proxy_port is None

        await mem_engine.dispose()


@pytest.mark.unit
class TestZiniaoNormalModeRecovery:
    """Failure point (post-upgrade): a fanout that finds Ziniao in GUI/
    normal mode must relaunch it into WebDriver mode — once, coordinated —
    instead of failing the task. See docs/ziniao-concurrency.md."""

    def _acct(self):
        a = mock.MagicMock()
        a.company, a.username, a.encrypted_password = 'c', 'u', 'enc'
        a.socket_port, a.client_path = 16851, 'ziniao'
        return a

    @pytest.mark.asyncio
    async def test_normal_mode_relaunches_webdriver_not_fail(self):
        mgr = BrowserManager()
        store = _make_fake_store()
        store.ziniao_account_id = 'acct-1'
        db = mock.AsyncMock()
        db.get = mock.AsyncMock(return_value=self._acct())

        calls = {'n': 0}

        async def fake_ensure(**kw):
            calls['n'] += 1
            if calls['n'] <= 2:  # initial + re-check-under-lock still normal
                raise ZiniaoNormalModeError('normal mode')
            # 3rd call (after relaunch) succeeds

        with (
            mock.patch(
                'app.browser.manager.ensure_ziniao_running',
                side_effect=fake_ensure,
            ),
            mock.patch(
                'app.browser.manager.kill_and_relaunch_ziniao',
                new=mock.AsyncMock(),
            ) as kar,
            mock.patch(
                'app.browser.manager.decrypt_password', return_value='pw'
            ),
        ):
            # Must NOT raise — recovers by relaunching into WebDriver.
            await mgr.check_ziniao_reachable(store, db)

        kar.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_peer_already_relaunched_no_double_kill(self):
        mgr = BrowserManager()
        store = _make_fake_store()
        store.ziniao_account_id = 'acct-1'
        db = mock.AsyncMock()
        db.get = mock.AsyncMock(return_value=self._acct())

        calls = {'n': 0}

        async def fake_ensure(**kw):
            calls['n'] += 1
            if calls['n'] == 1:  # initial: normal; a peer fixes it before lock
                raise ZiniaoNormalModeError('normal mode')
            # re-check under lock succeeds → no relaunch needed

        with (
            mock.patch(
                'app.browser.manager.ensure_ziniao_running',
                side_effect=fake_ensure,
            ),
            mock.patch(
                'app.browser.manager.kill_and_relaunch_ziniao',
                new=mock.AsyncMock(),
            ) as kar,
            mock.patch(
                'app.browser.manager.decrypt_password', return_value='pw'
            ),
        ):
            await mgr.check_ziniao_reachable(store, db)

        kar.assert_not_awaited()  # peer already relaunched — no double kill
