"""The reservation window must not log "opening handshake failed" (84's
final regression, found on every store start after #340).

``proxy_ports._reserve_socket`` used to bind AND LISTEN from
allocation until ``websockets.serve`` took the socket over. The
wrapper's ownership probe (``curl --max-time 2 …/vibe/instance``)
connected into that backlog, got no answer, timed out and closed —
and the mux then accepted the dead connection and logged one ERROR
per store start.

The fix: bind WITHOUT listening (probes get a prompt ECONNREFUSED)
and without SO_REUSEADDR (a second bind of the port fails with
EADDRINUSE no matter what the other socket sets — on Linux the
silent double-bind needs SO_REUSEADDR on BOTH sockets). The mux calls
``listen()`` itself right before ``websockets.serve``.
"""

import asyncio
import errno
import socket
import time

import pytest

from app.browser.cdp_mux_proxy import CDPMuxProxy
from app.browser.proxy_ports import ProxyPortAllocator

pytestmark = [pytest.mark.unit]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class TestReservationWindow:
    def test_a_probe_during_the_reservation_is_refused_promptly(self):
        """Brief test 1: a client connect during the reservation gets a
        prompt, clean result (< 0.5 s) — on main's bind+listen it
        hung in the backlog until its timeout (the negative control:
        the same connect against a LISTENING socket reaches its
        0.5 s timeout below)."""
        port = _free_port()
        sock = ProxyPortAllocator._reserve_socket(port)
        assert sock is not None
        try:
            # No settimeout: a refused connect fails immediately on
            # every platform (a timeout would put the socket in
            # non-blocking mode on Windows, where connect_ex then
            # reports WSAEWOULDBLOCK instead of the refusal).
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as c:
                t0 = time.monotonic()
                rc = c.connect_ex(('127.0.0.1', port))
                elapsed = time.monotonic() - t0
            assert rc == errno.ECONNREFUSED
            assert elapsed < 0.5
        finally:
            sock.close()

    def test_negative_control_main_hangs_in_the_backlog(self):
        """What main's bind+listen did: the connect succeeds into the
        backlog and the read hangs to its timeout — the dead
        connection the mux later accepted and logged about."""
        port = _free_port()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('127.0.0.1', port))
        sock.listen(16)  # main's shape
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as c:
                c.settimeout(0.3)
                assert c.connect_ex(('127.0.0.1', port)) == 0
                with pytest.raises(socket.timeout):
                    c.recv(64)  # no answer: the backlog holds us
        finally:
            sock.close()

    def test_a_second_reservation_of_the_same_port_fails(self):
        """Brief test 2: while one reservation holds the port, a second
        reservation (and a plain bind, with AND without SO_REUSEADDR)
        of the same port FAILS — the atomic-allocation guarantee
        survives dropping listen()."""
        port = _free_port()
        first = ProxyPortAllocator._reserve_socket(port)
        assert first is not None
        try:
            assert ProxyPortAllocator._reserve_socket(port) is None

            plain = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                with pytest.raises(OSError):
                    plain.bind(('127.0.0.1', port))
            finally:
                plain.close()

            reuse = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                reuse.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                # The Linux double-bind needs SO_REUSEADDR on BOTH
                # sockets; ours does not set it, so this must fail.
                with pytest.raises(OSError):
                    reuse.bind(('127.0.0.1', port))
            finally:
                reuse.close()
        finally:
            first.close()

    async def test_the_handoff_serves_the_instance_endpoint(self):
        """Brief test 3: websockets.serve on the reserved (bound, not
        listening) socket serves /vibe/instance after the mux's
        listen() — the handoff still works end to end."""
        port = _free_port()
        sock = ProxyPortAllocator._reserve_socket(port)
        assert sock is not None
        proxy = CDPMuxProxy(
            listen_port=port,
            target_port=port,
            instance_id='handoff-check',
            listen_sock=sock,
        )

        # Skip the upstream connection: only the HTTP layer matters
        # here (the mux's listen() + serve on the reserved socket).
        async def _noop():
            return None

        proxy._connect_upstream_and_cleanup = _noop
        proxy._startup_cleanup = _noop
        proxy._startup_download_behavior = _noop
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(
                b'GET /vibe/instance HTTP/1.1\r\nHost: x\r\n'
                b'Connection: close\r\n\r\n'
            )
            await writer.drain()
            data = await asyncio.wait_for(reader.read(256), timeout=2)
            assert b'200' in data[:50]
            assert b'handoff-check' in data
        finally:
            await proxy.stop()
