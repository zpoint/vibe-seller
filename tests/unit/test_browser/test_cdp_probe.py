"""Every CDP-proxy liveness probe reads a healthy proxy as alive.

The proxy is a ``websockets`` server, which rejects an HTTP/1.0 request
line without answering. The browser manager's own probe still sent 1.0,
so reusing a running store session always failed the probe and the
manager force-restarted the proxy, dropping every live CDP client on
that store. Probed here against the same server library the proxy uses.

Since PR C1 the manager's probe is an OWNERSHIP probe: the listener
must also answer ``/vibe/instance`` with THIS server instance's id, so
another server's (or an agent-spawned bridge's) listener is never
reused. The aux browser's probe stays plain liveness — aux browsers
listen on ephemeral, kernel-chosen ports and carry no instance id.
"""

import json

import pytest
import websockets

from app.browser import aux_browser
from app.browser.manager import BrowserManager

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _responder(instance_id: str):
    """Answer like a CDPMuxProxy of *instance_id* would."""

    async def _answer(connection, request):
        path = request.path.split('?', 1)[0]
        if path == '/vibe/instance':
            return connection.respond(
                200, json.dumps({'instance': instance_id})
            )
        if path.startswith('/json'):
            return connection.respond(200, '{}')
        return None

    return _answer


class TestManagerProbeOwnership:
    async def test_a_healthy_own_proxy_reads_as_alive(self):
        mgr = BrowserManager()
        async with websockets.serve(
            lambda ws: None,
            '127.0.0.1',
            0,
            process_request=_responder(mgr.instance_id),
        ) as server:
            port = server.sockets[0].getsockname()[1]
            assert await mgr._cdp_alive(port) is True

    async def test_a_foreign_listener_reads_as_dead(self):
        """A healthy proxy of ANOTHER server instance must read as dead,
        not be reused — the cross-attach this check exists for."""
        mgr = BrowserManager()
        async with websockets.serve(
            lambda ws: None,
            '127.0.0.1',
            0,
            process_request=_responder('another-server'),
        ) as server:
            port = server.sockets[0].getsockname()[1]
            assert await mgr._cdp_alive(port) is False

    async def test_a_listener_without_the_handshake_reads_as_dead(self):
        """An agent-spawned CDP bridge serves /json but no /vibe/instance
        — foreign by construction, never attached to."""

        async def _answer(connection, request):
            if request.path.startswith('/json'):
                return connection.respond(200, '{}')
            return None

        mgr = BrowserManager()
        async with websockets.serve(
            lambda ws: None, '127.0.0.1', 0, process_request=_answer
        ) as server:
            port = server.sockets[0].getsockname()[1]
            assert await mgr._cdp_alive(port) is False

    async def test_a_closed_port_reads_as_dead(self):
        mgr = BrowserManager()
        assert await mgr._cdp_alive(1) is False


class TestAuxProbeLiveness:
    """The aux probe stays plain liveness (ephemeral ports, no id)."""

    async def test_a_healthy_websockets_proxy_reads_as_alive(self):
        async with websockets.serve(
            lambda ws: None,
            '127.0.0.1',
            0,
            process_request=_responder('any'),
        ) as server:
            port = server.sockets[0].getsockname()[1]
            assert await aux_browser._alive(port) is True

    async def test_a_closed_port_reads_as_dead(self):
        assert await aux_browser._alive(1) is False
