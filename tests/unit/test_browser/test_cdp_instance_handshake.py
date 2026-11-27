"""The CDP-proxy ownership handshake (PR C1).

``CDPMuxProxy`` serves ``GET /vibe/instance`` with the id of the server
instance that started it, and ``app.browser.cdp_probe.proxy_ownership``
turns that into a tri-state answer. Together they are what lets two
vibe-seller servers on one machine (and agent-spawned CDP bridges
squatting in the proxy range) tell each other apart — liveness alone
cross-attached one server's agent onto another server's browser.
"""

import json

import pytest
import websockets

from app.browser.cdp_mux_proxy import CDPMuxProxy
from app.browser.cdp_probe import FOREIGN, OURS, proxy_ownership

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _serve_proxy(instance_id: str | None):
    """A listening CDPMuxProxy HTTP layer (no upstream needed — only
    ``process_request`` serves the handshake)."""
    proxy = CDPMuxProxy(listen_port=0, target_port=0, instance_id=instance_id)
    server = await websockets.serve(
        lambda ws: None,
        '127.0.0.1',
        0,
        process_request=proxy._process_http,
    )
    port = server.sockets[0].getsockname()[1]
    return proxy, server, port


class TestInstanceEndpoint:
    async def test_serves_the_instance_id(self):
        _proxy, server, port = await _serve_proxy('srv-abc')
        async with server:
            assert await proxy_ownership(port, 'srv-abc') == OURS

    async def test_another_instance_is_foreign(self):
        _proxy, server, port = await _serve_proxy('srv-abc')
        async with server:
            assert await proxy_ownership(port, 'srv-other') == FOREIGN

    async def test_no_instance_id_serves_nothing_to_confirm(self):
        """A proxy without an id (aux browsers) must read as foreign to
        every probe — fail safe, never attach."""
        _proxy, server, port = await _serve_proxy(None)
        async with server:
            assert await proxy_ownership(port, 'whatever') == FOREIGN

    async def test_a_closed_port_is_free(self):
        # A port nothing listens on: refused at once on almost every
        # host (FREE); the odd host whose unbound loopback connect
        # hangs instead answers FOREIGN after the timeout. Either way
        # it is never OURS — the only answer that would attach.
        assert await proxy_ownership(1, 'x') != OURS

    async def test_the_body_is_json_with_the_id(self):
        """The endpoint's exact shape — the wrapper greps for
        ``"<id>"`` in the body, so it must stay compact JSON."""
        proxy = CDPMuxProxy(listen_port=0, target_port=0, instance_id='srv-abc')

        class _Req:
            path = '/vibe/instance'

        resp = await proxy._process_http(None, _Req())  # type: ignore[arg-type]
        assert resp is not None
        assert json.loads(resp.body) == {'instance': 'srv-abc'}
        assert b'"srv-abc"' in resp.body
