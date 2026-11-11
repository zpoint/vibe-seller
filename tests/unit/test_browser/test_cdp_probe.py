"""Every CDP-proxy liveness probe reads a healthy proxy as alive.

The proxy is a ``websockets`` server, which rejects an HTTP/1.0 request
line without answering. The browser manager's own probe still sent 1.0,
so reusing a running store session always failed the probe and the
manager force-restarted the proxy, dropping every live CDP client on
that store. Probed here against the same server library the proxy uses.
"""

import pytest
import websockets

from app.browser import aux_browser
from app.browser.manager import BrowserManager

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

PROBES = {
    'manager': BrowserManager._cdp_alive,
    'aux': aux_browser._alive,
}


async def _answer_json(connection, request):
    if request.path.startswith('/json'):
        return connection.respond(200, '{}')
    return None


@pytest.mark.parametrize('name', sorted(PROBES))
async def test_a_healthy_websockets_proxy_reads_as_alive(name):
    async with websockets.serve(
        lambda ws: None, '127.0.0.1', 0, process_request=_answer_json
    ) as server:
        port = server.sockets[0].getsockname()[1]
        assert await PROBES[name](port) is True


@pytest.mark.parametrize('name', sorted(PROBES))
async def test_a_closed_port_reads_as_dead(name):
    assert await PROBES[name](1) is False
