"""
Integration tests for CDPMuxProxy.

Chromium launched once per module (_browser fixture); proxy is
per-test for clean state isolation.

    Chromium (module-scoped, single port)
      ├── CDPMuxProxy (per-test, ephemeral port)
      │   ├── Client A (/client-task-a)
      │   └── Client B (/client-task-b)
      └── Direct Client (browser port, bypasses proxy)
"""

import asyncio
import json
import socket

import aiohttp
import pytest
import websockets

from app.browser.cdp_mux_proxy import CDPMuxProxy
from tests.integration.conftest import cleanup_browser_tabs


def _free_port() -> int:
    """Find a free ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def send_cdp(ws, method, params=None, session_id=None):
    """Send a CDP command and wait for the response."""
    msg = {
        'id': getattr(send_cdp, '_counter', 0) + 1,
        'method': method,
    }
    send_cdp._counter = msg['id']
    if params:
        msg['params'] = params
    if session_id:
        msg['sessionId'] = session_id
    await ws.send(json.dumps(msg))

    # Wait for response with matching id
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = json.loads(raw)
        if resp.get('id') == msg['id']:
            return resp
        # Events may arrive before the response — skip them


async def collect_events(ws, timeout=1.0):
    """Collect all events available within timeout."""
    events = []
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            events.append(json.loads(raw))
    except TimeoutError:
        pass
    return events


async def get_target_ids(ws):
    """Get list of page target IDs via Target.getTargets."""
    resp = await send_cdp(ws, 'Target.getTargets')
    targets = resp.get('result', {}).get('targetInfos', [])
    return [t['targetId'] for t in targets if t.get('type') == 'page']


async def create_page(ws, url='about:blank'):
    """Create a new page and return its targetId."""
    resp = await send_cdp(ws, 'Target.createTarget', {'url': url})
    return resp.get('result', {}).get('targetId')


async def connect_client(port, client_id):
    """Connect a client to the proxy."""
    ws = await websockets.connect(
        f'ws://127.0.0.1:{port}/client-{client_id}',
        max_size=50 * 1024 * 1024,
    )
    return ws


async def connect_direct(port):
    """Connect directly to the browser (bypass proxy)."""
    url = f'http://127.0.0.1:{port}/json/version'
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            ws_url = data['webSocketDebuggerUrl']
    return await websockets.connect(ws_url, max_size=50 * 1024 * 1024)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def browser_and_proxy(_browser):
    """CDPMuxProxy on an ephemeral port, backed by module-scoped browser."""
    browser_port = _browser
    proxy_port = _free_port()
    p = CDPMuxProxy(
        listen_port=proxy_port,
        target_port=browser_port,
        max_clients=5,
        cleanup_grace=0,
    )
    await p.start()
    p._test_proxy_port = proxy_port
    p._test_browser_port = browser_port
    yield p
    await p.stop()
    await cleanup_browser_tabs(browser_port)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


@pytest.mark.integration
class TestCDPMuxProxyIsolation:
    """Test multi-client isolation through the proxy."""

    @pytest.mark.asyncio
    async def test_client_a_tab_invisible_to_client_b(self, browser_and_proxy):
        """Client A's tab should NOT appear in Client B's
        getTargets, but SHOULD appear in Direct Client's."""
        client_a = await connect_client(
            browser_and_proxy._test_proxy_port, 'task-a'
        )
        client_b = await connect_client(
            browser_and_proxy._test_proxy_port, 'task-b'
        )
        direct = await connect_direct(browser_and_proxy._test_browser_port)

        try:
            # Client A creates a page
            target_a = await create_page(client_a)
            assert target_a is not None

            # Client B should NOT see it
            b_targets = await get_target_ids(client_b)
            assert target_a not in b_targets

            # Direct client SHOULD see it (proves proxy filters)
            d_targets = await get_target_ids(direct)
            assert target_a in d_targets
        finally:
            await client_a.close()
            await client_b.close()
            await direct.close()
            # Wait for cleanup
            await asyncio.sleep(0.5)

    @pytest.mark.asyncio
    async def test_block_cross_client_attach(self, browser_and_proxy):
        """Client B cannot attach to Client A's target."""
        client_a = await connect_client(
            browser_and_proxy._test_proxy_port, 'task-a2'
        )
        client_b = await connect_client(
            browser_and_proxy._test_proxy_port, 'task-b2'
        )

        try:
            target_a = await create_page(client_a)

            # Client B tries to attach — should get error
            resp = await send_cdp(
                client_b,
                'Target.attachToTarget',
                {
                    'targetId': target_a,
                    'flatten': True,
                },
            )
            assert 'error' in resp
            assert 'another client' in resp['error']['message']
        finally:
            await client_a.close()
            await client_b.close()
            await asyncio.sleep(0.5)

    @pytest.mark.asyncio
    async def test_independent_tab_counts(self, browser_and_proxy):
        """Each client sees only its own tabs."""
        client_a = await connect_client(
            browser_and_proxy._test_proxy_port, 'task-a3'
        )
        client_b = await connect_client(
            browser_and_proxy._test_proxy_port, 'task-b3'
        )
        direct = await connect_direct(browser_and_proxy._test_browser_port)

        try:
            # A opens 2 tabs, B opens 1
            ta1 = await create_page(client_a)
            ta2 = await create_page(client_a)
            tb1 = await create_page(client_b)

            a_targets = await get_target_ids(client_a)
            b_targets = await get_target_ids(client_b)
            d_targets = await get_target_ids(direct)

            # A sees its 2 (+ possibly unowned initial tabs)
            assert ta1 in a_targets
            assert ta2 in a_targets
            assert tb1 not in a_targets

            # B sees its 1
            assert tb1 in b_targets
            assert ta1 not in b_targets
            assert ta2 not in b_targets

            # Direct sees all 3
            assert ta1 in d_targets
            assert ta2 in d_targets
            assert tb1 in d_targets
        finally:
            await client_a.close()
            await client_b.close()
            await direct.close()
            await asyncio.sleep(0.5)

    @pytest.mark.asyncio
    async def test_disconnect_cleanup(self, browser_and_proxy):
        """When Client A disconnects, its tabs are closed."""
        client_a = await connect_client(
            browser_and_proxy._test_proxy_port, 'task-a4'
        )
        direct = await connect_direct(browser_and_proxy._test_browser_port)

        try:
            ta1 = await create_page(client_a)
            ta2 = await create_page(client_a)

            # Verify they exist
            d_before = await get_target_ids(direct)
            assert ta1 in d_before
            assert ta2 in d_before

            # Disconnect A
            await client_a.close()
            await asyncio.sleep(1.0)  # Wait for cleanup

            # Tabs should be gone
            d_after = await get_target_ids(direct)
            assert ta1 not in d_after
            assert ta2 not in d_after
        finally:
            await direct.close()

    @pytest.mark.asyncio
    async def test_crash_cleanup_no_leak(self, browser_and_proxy):
        """Forceful disconnect (crash) also cleans up."""
        client_a = await connect_client(
            browser_and_proxy._test_proxy_port, 'task-a5'
        )
        direct = await connect_direct(browser_and_proxy._test_browser_port)

        try:
            ta = await create_page(client_a)

            d_before = await get_target_ids(direct)
            assert ta in d_before

            # Force-close (simulates crash)
            client_a.transport.close()
            await asyncio.sleep(1.0)

            d_after = await get_target_ids(direct)
            assert ta not in d_after
        finally:
            await direct.close()

    @pytest.mark.asyncio
    async def test_auto_attach_events_routed_correctly(self, browser_and_proxy):
        """setAutoAttach events route to the correct client."""
        client_a = await connect_client(
            browser_and_proxy._test_proxy_port, 'task-a6'
        )
        client_b = await connect_client(
            browser_and_proxy._test_proxy_port, 'task-b6'
        )

        try:
            # Enable auto-attach on both clients
            await send_cdp(
                client_a,
                'Target.setAutoAttach',
                {
                    'autoAttach': True,
                    'waitForDebuggerOnStart': False,
                    'flatten': True,
                },
            )
            await send_cdp(
                client_b,
                'Target.setAutoAttach',
                {
                    'autoAttach': True,
                    'waitForDebuggerOnStart': False,
                    'flatten': True,
                },
            )

            # Drain any setup events
            await collect_events(client_a, timeout=0.5)
            await collect_events(client_b, timeout=0.5)

            # A creates a page — use raw send so we collect ALL
            # messages (including events) rather than just response
            msg_id = getattr(send_cdp, '_counter', 0) + 1
            send_cdp._counter = msg_id
            await client_a.send(
                json.dumps({
                    'id': msg_id,
                    'method': 'Target.createTarget',
                    'params': {'url': 'about:blank'},
                })
            )

            # Collect all messages from A (response + events)
            a_msgs = await collect_events(client_a, timeout=3.0)

            # Find the targetId from createTarget response
            ta = None
            for m in a_msgs:
                if m.get('id') == msg_id:
                    ta = m.get('result', {}).get('targetId')
                    break
            assert ta, f'createTarget response not found in: {a_msgs}'

            # B should NOT get any events for A's page
            b_events = await collect_events(client_b, timeout=1.0)

            a_attached = [
                e
                for e in a_msgs
                if e.get('method') == 'Target.attachedToTarget'
                and e.get('params', {}).get('targetInfo', {}).get('targetId')
                == ta
            ]
            b_attached = [
                e
                for e in b_events
                if e.get('method') == 'Target.attachedToTarget'
                and e.get('params', {}).get('targetInfo', {}).get('targetId')
                == ta
            ]

            assert len(a_attached) >= 1, (
                f'Client A should get attachedToTarget. Got: {a_msgs}'
            )
            assert len(b_attached) == 0, (
                'Client B should NOT get attachedToTarget for A'
            )
        finally:
            await client_a.close()
            await client_b.close()
            await asyncio.sleep(0.5)

    @pytest.mark.asyncio
    async def test_max_clients_enforced(self, browser_and_proxy):
        """Proxy rejects connections beyond max_clients."""
        clients = []
        try:
            for i in range(5):
                c = await connect_client(
                    browser_and_proxy._test_proxy_port, f'task-limit-{i}'
                )
                clients.append(c)

            # 6th should be rejected
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                c6 = await connect_client(
                    browser_and_proxy._test_proxy_port, 'task-limit-5'
                )
                # Try to send — should fail
                await send_cdp(c6, 'Target.getTargets')
        finally:
            for c in clients:
                await c.close()
            await asyncio.sleep(0.5)
