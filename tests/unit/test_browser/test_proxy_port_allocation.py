"""Bind-before-use proxy-port allocation (PR C1).

Two vibe-seller servers on one machine used to share CDP proxy ports:
the allocator trusted its own counter and the DB, and the wrapper's
liveness probe accepted whatever answered — a test task on one server
drove another server's store browser. The allocator now BINDS each
port it hands out (the store's mux later serves on that very socket),
skips any port someone else holds, and re-adopts a DB-saved port only
when the bind proves it free. On a single-instance machine the ports
are exactly what they were.
"""

import asyncio
import json
import socket
from unittest import mock

import pytest
import websockets

from app.browser import aux_browser as aux_browser_mod, manager as manager_mod
from app.browser.manager import BrowserManager
from app.browser.proxy_ports import (
    default_proxy_base,
    resolve_proxy_base,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _free_port() -> int:
    """A port nothing is listening on right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class _ForeignListener:
    """A listener that answers /vibe/instance like ANOTHER server's
    CDPMuxProxy (or an agent-spawned CDP bridge: pass instance_id=None
    and it serves nothing vibe-specific at all)."""

    def __init__(self, instance_id: str | None):
        self._instance_id = instance_id
        self._server = None

    async def __aenter__(self) -> int:
        id_ = self._instance_id

        async def _answer(connection, request):
            path = request.path.split('?', 1)[0]
            if id_ is not None and path == '/vibe/instance':
                return connection.respond(200, json.dumps({'instance': id_}))
            if path.startswith('/json'):
                return connection.respond(200, '{}')
            return None

        self._server = await websockets.serve(
            lambda ws: None, '127.0.0.1', 0, process_request=_answer
        )
        return self._server.sockets[0].getsockname()[1]

    async def __aexit__(self, *a):
        self._server.close()
        await self._server.wait_closed()
        return False


def _manager_at(port: int) -> BrowserManager:
    """A manager whose walk starts at *port* (deterministic — the base
    port on a shared dev machine may be held by another session)."""
    mgr = BrowserManager()
    mgr.ports.next_port = port
    return mgr


class TestBindBeforeUse:
    async def test_two_managers_never_collide_even_racing(self):
        """D-C1-1: the allocation is atomic with the bind. Two managers
        allocating at the same moment (asyncio.gather, like two servers
        on one machine) get distinct ports, and both are bound."""
        base = _free_port()
        mgr_a, mgr_b = _manager_at(base), _manager_at(base)
        pa, pb = await asyncio.gather(
            mgr_a.ports.allocate('store-a'),
            mgr_b.ports.allocate('store-b'),
        )
        assert pa != pb
        # One manager won the base bind; the other's bind failed and it
        # walked to a DIFFERENT port (whatever the machine's noise
        # around the ephemeral base — the exact neighbour is not the
        # contract). Both hold live reservations.
        assert base in (pa, pb)
        assert pa in mgr_a.ports.sockets
        assert pb in mgr_b.ports.sockets

    async def test_allocated_ports_are_bound_not_just_probed(self):
        base = _free_port()
        mgr = _manager_at(base)
        port = await mgr.ports.allocate('store-a')
        # The reservation socket is listening: another process cannot
        # bind it, and it answers no HTTP (nothing there yet).
        assert port in mgr.ports.sockets
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            assert s.connect_ex(('127.0.0.1', port)) == 0

    async def test_a_foreign_server_on_the_candidate_is_skipped(self):
        """Another server's proxy on the preferred port → next port."""
        async with _ForeignListener('other-server') as foreign_port:
            mgr = _manager_at(foreign_port)
            port = await mgr.ports.allocate('store-a')
            assert port != foreign_port

    async def test_an_agent_spawned_bridge_is_skipped(self):
        """A listener with NO vibe handshake at all (the observed
        BitBrowser-agent ``cdp_bridge.py`` on 127.0.0.1:9222) is
        foreign by construction."""
        async with _ForeignListener(None) as squatter:
            mgr = _manager_at(squatter)
            port = await mgr.ports.allocate('store-a')
            assert port != squatter

    async def test_another_servers_mux_is_not_adopted_as_ours(self):
        """A listener answering with a DIFFERENT instance id must never
        read as 'ours' to this manager — per-instance ids, not one id
        per process (two managers in one process are two servers)."""
        async with _ForeignListener('other-server') as foreign_port:
            mgr = _manager_at(_free_port())
            assert not await mgr._cdp_alive(foreign_port)


class TestSavedPorts:
    async def test_a_free_saved_port_is_adopted(self):
        """Single-instance behaviour unchanged: after a restart the DB
        port is re-adopted when free."""
        saved = _free_port()
        mgr = _manager_at(_free_port())
        assert await mgr.ports.allocate('s1', saved) == saved
        assert mgr.ports.sockets[saved] is not None

    async def test_a_foreign_saved_port_is_reallocated(self):
        """A DB-saved port now held by another server/listener is never
        reused blindly — the store moves to a fresh port."""
        async with _ForeignListener('other-server') as held:
            mgr = _manager_at(_free_port())
            port = await mgr.ports.allocate('s1', held)
            assert port != held
            assert mgr.ports.ports['s1'] == port

    async def test_a_saved_port_of_this_manager_is_not_handed_twice(self):
        """A saved port already allocated to another store of THIS
        server is not adopted by a second store (bind would fail on
        the reservation)."""
        base = _free_port()
        mgr = _manager_at(base)
        first = await mgr.ports.allocate('store-a')
        second = await mgr.ports.allocate('store-b', first)
        assert second != first


class TestSingleInstanceNoChange:
    async def test_sequential_ports_from_the_base(self):
        """The Ziniao no-regression pin: on a single-instance machine
        (no foreign listeners) stores keep getting sequential ports
        from the base — exactly main's behaviour."""
        base = _free_port()
        mgr = _manager_at(base)
        p1 = await mgr.ports.allocate('store-a')
        p2 = await mgr.ports.allocate('store-b')
        p3 = await mgr.ports.allocate('store-c')
        assert [p1, p2, p3] == [base, base + 1, base + 2]

    async def test_the_base_is_9222_outside_demo_mode(self):
        """Non-demo machines keep 9222 as the preferred start — the
        constant itself, so a change to the base is a conscious one."""
        if manager_mod._BASE_PROXY_PORT != 9222:
            pytest.skip('demo-mode machines start at 9322')
        assert manager_mod._BASE_PROXY_PORT == 9222


class TestReservationTeardown:
    async def test_stop_frees_the_reservation_of_a_never_started_browser(self):
        """A wrapper-only allocation (browser never started) holds a
        listening reservation; stopping the session frees the port."""
        base = _free_port()
        mgr = _manager_at(base)
        port = await mgr.ports.allocate('store-a')
        store = mock.MagicMock()
        store.id, store.name = 'store-a', 'Store A'

        fake_db = mock.AsyncMock()
        fake_db.execute = mock.AsyncMock(
            return_value=mock.MagicMock(
                scalar_one_or_none=mock.MagicMock(return_value=None)
            )
        )
        with mock.patch('app.browser.manager.remove_browser_use_wrapper'):
            await mgr._stop_session_locked(store, fake_db)
        assert port not in mgr.ports.sockets
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            assert s.connect_ex(('127.0.0.1', port)) != 0

    async def test_stop_session_stops_a_backend_with_no_active_record(self):
        """The idle-sweep mux leak: a store whose active-session record
        was lost still has a backend whose mux LISTENS. Teardown must
        stop it regardless — the old ``if info:`` guard skipped the
        whole stop and the listener outlived the browser by hours."""

        class _LeakyBackend:
            def __init__(self):
                self.stopped = False
                # A REAL listener, like a leaked CDPMuxProxy server.
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.bind(('127.0.0.1', 0))
                self.sock.listen(4)
                self.port = self.sock.getsockname()[1]

            async def stop(self, info):
                self.stopped = True
                self.sock.close()

        mgr = BrowserManager()
        store = mock.MagicMock()
        store.id, store.name = 'store-a', 'Store A'
        backend = _LeakyBackend()
        mgr._backends[store.id] = backend
        # The active-session record is GONE (an earlier failed stop).
        assert store.id not in mgr._active_sessions

        fake_db = mock.AsyncMock()
        fake_db.execute = mock.AsyncMock(
            return_value=mock.MagicMock(
                scalar_one_or_none=mock.MagicMock(return_value=None)
            )
        )
        with mock.patch('app.browser.manager.remove_browser_use_wrapper'):
            await mgr._stop_session_locked(store, fake_db)

        assert backend.stopped
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            assert s.connect_ex(('127.0.0.1', backend.port)) != 0

    async def test_a_failed_backend_start_releases_the_reservation(self):
        """A start that never served must not keep the port reserved —
        the next attempt re-allocates from a clean slate."""
        base = _free_port()
        mgr = _manager_at(base)
        port = await mgr.ports.allocate('store-a')

        failing = mock.AsyncMock()
        failing.start = mock.AsyncMock(side_effect=RuntimeError('nope'))
        with (
            mock.patch.object(mgr, '_get_backend', return_value=failing),
            mock.patch('app.browser.manager.write_browser_use_wrapper'),
            mock.patch('app.browser.manager.create_token', return_value='tok'),
        ):
            store = mock.MagicMock()
            store.id, store.name = 'store-a', 'Store A'
            store.browser_backend = 'chrome'
            store.browser_config = '{}'
            store.ziniao_account_id = None
            store.browser_oauth = None
            store.slug = 'store-a'
            fake_db = mock.AsyncMock()
            fake_db.execute = mock.AsyncMock(
                return_value=mock.MagicMock(
                    scalar_one_or_none=mock.MagicMock(return_value=None)
                )
            )
            fake_db.get = mock.AsyncMock(return_value=None)
            with pytest.raises(RuntimeError, match='nope'):
                await mgr._start_session_locked(store, fake_db)

        assert port not in mgr.ports.sockets
        assert 'store-a' not in mgr.ports.ports
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            assert s.connect_ex(('127.0.0.1', port)) != 0


class TestPerInstanceBase:
    """``VIBE_CDP_PROXY_BASE`` (user decision 2026-11-27): parallel test
    servers each get their own port RANGE instead of queueing for one
    9222 holder. Unset = today's default exactly."""

    async def test_the_env_base_is_honoured(self, monkeypatch):
        # A runtime-free port as the base: the first allocation IS the
        # base (a hard-coded 95xx would collide with C1's own live
        # proof servers on this machine).
        base = _free_port()
        monkeypatch.setenv('VIBE_CDP_PROXY_BASE', str(base))
        mgr = BrowserManager()
        first = await mgr.ports.allocate('store-a')
        second = await mgr.ports.allocate('store-b')
        assert first == base
        assert second != first and second >= base

    async def test_garbage_falls_back_to_the_default(self, monkeypatch):
        for garbage in ('banana', '', '  ', '9,500'):
            monkeypatch.setenv('VIBE_CDP_PROXY_BASE', garbage)
            assert resolve_proxy_base() == default_proxy_base(), garbage

    async def test_out_of_range_falls_back(self, monkeypatch):
        for bad in ('1023', '65001', '-1', '999999'):
            monkeypatch.setenv('VIBE_CDP_PROXY_BASE', bad)
            assert resolve_proxy_base() == default_proxy_base(), bad

    async def test_unset_is_the_default_exactly(self, monkeypatch):
        monkeypatch.delenv('VIBE_CDP_PROXY_BASE', raising=False)
        assert resolve_proxy_base() == default_proxy_base()

    async def test_two_managers_with_different_bases_never_collide(
        self, monkeypatch
    ):
        base_a, base_b = _free_port(), _free_port()
        monkeypatch.setenv('VIBE_CDP_PROXY_BASE', str(base_a))
        mgr_a = BrowserManager()
        monkeypatch.setenv('VIBE_CDP_PROXY_BASE', str(base_b))
        mgr_b = BrowserManager()
        pa = await mgr_a.ports.allocate('store-a')
        pb = await mgr_b.ports.allocate('store-b')
        assert (pa, pb) == (base_a, base_b)
        # Neither manager ever hands out a port below its base.
        assert pa >= base_a
        assert pb >= base_b

    async def test_aux_ports_respect_the_base(self, monkeypatch):
        base = _free_port()
        monkeypatch.setenv('VIBE_CDP_PROXY_BASE', str(base))
        assert aux_browser_mod._aux_port() >= base
        # A base inside the ephemeral range exercises the floor walk.
        monkeypatch.setenv('VIBE_CDP_PROXY_BASE', '64000')
        assert aux_browser_mod._aux_port() >= 64000
