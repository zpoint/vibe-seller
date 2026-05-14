"""
Integration tests: ChromeBackend + CDPMuxProxy lifecycle.

Tests Chrome-specific behaviour that test_cdp_mux_browser_use.py
does not cover:
  1. Persistent user_data_dir — cookies survive proxy restart
  2. ChromeBackend.start() and stop() lifecycle
  3. Two clients navigate independently through Chrome proxy
  4. Shared cookie context across proxy clients
  5. Client disconnect cleans up tabs

All tests use real browsers (no mocking).
"""

import asyncio
import json
import socket

import aiohttp
from browser_use import BrowserSession
import pytest
import websockets

from app.browser.cdp_mux_proxy import CDPMuxProxy
from app.browser.chrome import PROFILES_DIR, ChromeBackend
from tests.integration.conftest import cleanup_browser_tabs

SESSION_TIMEOUT = 15


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------
# Helpers (reused from test_cdp_mux_browser_use.py pattern)
# ------------------------------------------------------------------


async def make_session(proxy_port, client_id):
    """Create+start a BrowserSession through the proxy."""
    s = BrowserSession(
        cdp_url=f'ws://127.0.0.1:{proxy_port}/client-{client_id}'
    )
    await asyncio.wait_for(s.start(), timeout=SESSION_TIMEOUT)
    return s


async def kill(session):
    """Kill session, suppress errors."""
    try:
        await asyncio.wait_for(session.kill(), timeout=2)
    except Exception:
        pass


async def kill_all(sessions):
    await asyncio.gather(*[kill(s) for s in sessions])


_cdp_counter_lock = asyncio.Lock()
_cdp_counter = 0


async def send_cdp(ws, method, params=None, session_id=None):
    """Send CDP command, wait for matching response."""
    global _cdp_counter
    async with _cdp_counter_lock:
        _cdp_counter += 1
        my_id = _cdp_counter
    msg = {'id': my_id, 'method': method}
    if params:
        msg['params'] = params
    if session_id:
        msg['sessionId'] = session_id
    await ws.send(json.dumps(msg))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = json.loads(raw)
        if resp.get('id') == my_id:
            return resp


async def wait_for_cdp(port: int, timeout: float = 30.0) -> None:
    """Poll CDP /json/version until ready."""
    url = f'http://127.0.0.1:{port}/json/version'
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as resp:
                    if resp.status == 200:
                        return
        except Exception:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError(f'Chrome CDP not ready at port {port} after {timeout}s')


async def direct_page_count(debug_port):
    """Count page targets via direct CDP."""
    async with aiohttp.ClientSession() as s:
        async with s.get(f'http://127.0.0.1:{debug_port}/json/version') as r:
            data = await r.json()
    ws = await websockets.connect(
        data['webSocketDebuggerUrl'], max_size=50 * 1024 * 1024
    )
    resp = await send_cdp(ws, 'Target.getTargets')
    targets = [
        t
        for t in resp.get('result', {}).get('targetInfos', [])
        if t.get('type') == 'page'
    ]
    await ws.close()
    return len(targets)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def chrome_env(_browser):
    """CDPMuxProxy backed by module-scoped browser.

    Yields dict with proxy and ports.
    """
    debug_port = _browser
    proxy_port = _free_port()
    proxy = CDPMuxProxy(
        listen_port=proxy_port,
        target_port=debug_port,
        max_clients=10,
        cleanup_grace=0,
    )
    await proxy.start()
    yield {
        'proxy': proxy,
        'proxy_port': proxy_port,
        'debug_port': debug_port,
    }
    await proxy.stop()
    await cleanup_browser_tabs(debug_port)


@pytest.fixture
async def chrome_env_with_profile(_browser):
    """CDPMuxProxy backed by the module-scoped browser.

    Name kept for historical/test-discovery continuity — this
    fixture no longer launches a per-test Chromium with a
    persistent ``user_data_dir``. It now just reuses the
    module-scoped ``_browser`` (same as ``chrome_env``); the
    "with_profile" suffix is vestigial and refers to the
    cookie-persistence-across-proxy-restart property the
    consuming test asserts.

    Used to test cookie persistence across proxy restarts.  The
    test only restarts the *proxy*, so the browser process must
    stay alive across `proxy.stop()` / new `proxy.start()` — the
    module-scoped `_browser` already provides exactly that, and
    cookies live in the running Chrome process regardless of
    whether the on-disk profile is persistent.

    Historically this fixture launched a second Chromium via
    ``pw.chromium.launch_persistent_context``, which caused
    intermittent SIGSEGVs on Linux CI runners (commit c408de5
    eliminated the same bug class for ``chrome_env`` but missed
    this one — see the integration-test failure on PR #155).
    """
    debug_port = _browser
    proxy_port = _free_port()
    proxy = CDPMuxProxy(
        listen_port=proxy_port,
        target_port=debug_port,
        max_clients=10,
        cleanup_grace=0,
    )
    await proxy.start()
    env = {
        'proxy': proxy,
        'proxy_port': proxy_port,
        'debug_port': debug_port,
    }
    yield env
    # Teardown — env['proxy'] may have been replaced by a test
    try:
        await env['proxy'].stop()
    except Exception:
        pass
    # Clear cookies set by the test so subsequent module-scoped
    # browser users (none today, but defence-in-depth for future
    # tests) start clean.
    try:
        async with aiohttp.ClientSession() as session:
            url = f'http://127.0.0.1:{debug_port}/json/version'
            async with session.get(url) as resp:
                data = await resp.json()
        ws = await websockets.connect(
            data['webSocketDebuggerUrl'], max_size=50 * 1024 * 1024
        )
        try:
            await send_cdp(ws, 'Storage.clearCookies')
        finally:
            await ws.close()
    except Exception:
        pass
    await cleanup_browser_tabs(debug_port)


# ==================================================================
# Group 1: ChromeBackend lifecycle
# ==================================================================


@pytest.mark.integration
class TestChromeBackendLifecycle:
    async def test_start_and_stop(self):
        """ChromeBackend.start() launches Chrome + proxy, stop() tears down."""
        backend = ChromeBackend()
        proxy_port = _free_port()
        info = await backend.start({
            'proxy_port': proxy_port,
            'headless': True,
            'store_slug': f'test-lifecycle-{_free_port()}',
        })
        try:
            assert info.cdp_port > 0
            # Proxy is reachable
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f'http://127.0.0.1:{proxy_port}/json/version',
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    assert resp.status == 200
        finally:
            await backend.stop(info)

        # After stop, proxy should not be reachable
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f'http://127.0.0.1:{proxy_port}/json/version',
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as resp:
                    # If we get here, proxy is still up (unexpected)
                    pytest.fail('Proxy still reachable after stop()')
        except (TimeoutError, aiohttp.ClientError, OSError):
            pass  # Expected — proxy is down

    async def test_start_creates_profile_dir(self):
        """ChromeBackend.start() creates user_data_dir for the store slug."""
        slug = f'test-profile-{_free_port()}'
        backend = ChromeBackend()
        proxy_port = _free_port()
        info = await backend.start({
            'proxy_port': proxy_port,
            'headless': True,
            'store_slug': slug,
        })
        try:
            profile_dir = PROFILES_DIR / slug
            assert profile_dir.exists()
            assert profile_dir.is_dir()
        finally:
            await backend.stop(info)


# ==================================================================
# Group 2: Independent navigation
# ==================================================================


@pytest.mark.integration
class TestChromeIndependentNavigation:
    async def test_two_clients_navigate_independently(self, chrome_env):
        """Two BrowserSessions through Chrome proxy navigate to
        different data: URLs without interference."""
        pp = chrome_env['proxy_port']
        sa = await make_session(pp, 'nav-a')
        sb = await make_session(pp, 'nav-b')
        try:
            pa = await sa.get_current_page()
            pb = await sb.get_current_page()
            await pa.goto('data:text/html,<h1>Page A</h1>')
            await pb.goto('data:text/html,<h1>Page B</h1>')
            # data: URLs may need a moment for DOM
            await asyncio.sleep(0.5)

            title_a = await pa.evaluate(
                '() => (document.querySelector("h1") || {}).textContent || ""'
            )
            title_b = await pb.evaluate(
                '() => (document.querySelector("h1") || {}).textContent || ""'
            )
            assert title_a == 'Page A'
            assert title_b == 'Page B'
        finally:
            await kill_all([sa, sb])


# ==================================================================
# Group 3: Shared cookie context
# ==================================================================


@pytest.mark.integration
class TestChromeSharedCookies:
    async def test_cookies_visible_across_clients(self, chrome_env):
        """Cookie set by client A is visible to client B
        (shared default browser context)."""
        pp = chrome_env['proxy_port']
        proxy = chrome_env['proxy']
        sa = await make_session(pp, 'ck-a')
        sb = await make_session(pp, 'ck-b')
        try:
            # Get session IDs from proxy internals
            sid_a = next(
                (s for s, c in proxy._session_to_client.items() if c == 'ck-a'),
                None,
            )
            sid_b = next(
                (s for s, c in proxy._session_to_client.items() if c == 'ck-b'),
                None,
            )
            assert sid_a and sid_b

            # Set cookie via A's raw CDP
            ws_a = await websockets.connect(
                f'ws://127.0.0.1:{pp}/client-ck-a-raw',
                max_size=50 * 1024 * 1024,
            )
            await send_cdp(
                ws_a,
                'Network.setCookie',
                {
                    'name': 'chrome_shared',
                    'value': 'hello',
                    'domain': 'example.com',
                    'path': '/',
                },
                session_id=sid_a,
            )
            await ws_a.close()

            # Read via B's raw CDP
            ws_b = await websockets.connect(
                f'ws://127.0.0.1:{pp}/client-ck-b-raw',
                max_size=50 * 1024 * 1024,
            )
            resp = await send_cdp(
                ws_b,
                'Network.getCookies',
                {'urls': ['https://example.com']},
                session_id=sid_b,
            )
            await ws_b.close()

            cookies = resp.get('result', {}).get('cookies', [])
            found = [c for c in cookies if c['name'] == 'chrome_shared']
            assert len(found) == 1
            assert found[0]['value'] == 'hello'
        finally:
            await kill_all([sa, sb])


# ==================================================================
# Group 4: Cleanup on disconnect
# ==================================================================


@pytest.mark.integration
class TestChromeDisconnectCleanup:
    async def test_disconnect_cleans_up_tabs(self, chrome_env):
        """Killing a session cleans up its tabs and proxy maps."""
        pp = chrome_env['proxy_port']
        proxy = chrome_env['proxy']
        s = await make_session(pp, 'dc-chrome')
        p = await s.get_current_page()
        await p.goto('data:text/html,<h1>cleanup</h1>')
        assert len(proxy._clients) == 1

        await kill(s)
        await asyncio.sleep(0.5)

        assert 'dc-chrome' not in proxy._clients
        # No orphan targets for this client
        orphans = [
            tid
            for tid, cid in proxy._target_to_client.items()
            if cid == 'dc-chrome'
        ]
        assert len(orphans) == 0

    async def test_one_disconnect_doesnt_affect_other(self, chrome_env):
        """Killing one session doesn't break the other."""
        pp = chrome_env['proxy_port']
        sa = await make_session(pp, 'surv-a')
        sb = await make_session(pp, 'surv-b')
        try:
            pa = await sa.get_current_page()
            pb = await sb.get_current_page()
            await pa.goto('data:text/html,<h1>A</h1>')
            await pb.goto('data:text/html,<h1>B</h1>')

            # Kill A
            await kill(sa)
            await asyncio.sleep(0.5)

            # B still works
            val = await pb.evaluate(
                '() => document.querySelector("h1").textContent'
            )
            assert val == 'B'
        finally:
            await kill_all([sa, sb])


# ==================================================================
# Group 5: Persistent profile — cookies survive proxy restart
# ==================================================================


@pytest.mark.integration
class TestChromePersistentProfile:
    async def test_cookies_survive_proxy_restart(self, chrome_env_with_profile):
        """Cookies set through the proxy persist after proxy
        restart (because Chrome keeps its user_data_dir)."""
        env = chrome_env_with_profile
        pp = env['proxy_port']
        dp = env['debug_port']
        proxy = env['proxy']

        # Set a cookie through the proxy
        s = await make_session(pp, 'persist')
        try:
            sid = next(
                (
                    sid
                    for sid, cid in proxy._session_to_client.items()
                    if cid == 'persist'
                ),
                None,
            )
            assert sid

            ws = await websockets.connect(
                f'ws://127.0.0.1:{pp}/client-persist-raw',
                max_size=50 * 1024 * 1024,
            )
            await send_cdp(
                ws,
                'Network.setCookie',
                {
                    'name': 'persist_test',
                    'value': 'survived',
                    'domain': 'example.com',
                    'path': '/',
                },
                session_id=sid,
            )
            await ws.close()
        finally:
            await kill(s)

        # Stop and restart the proxy (Chrome stays running)
        await proxy.stop()
        await asyncio.sleep(0.5)

        new_proxy = CDPMuxProxy(
            listen_port=pp,
            target_port=dp,
            max_clients=10,
            cleanup_grace=0,
        )
        await new_proxy.start()
        try:
            # Connect a new client and read the cookie
            s2 = await make_session(pp, 'persist2')
            try:
                sid2 = next(
                    (
                        sid
                        for sid, cid in new_proxy._session_to_client.items()
                        if cid == 'persist2'
                    ),
                    None,
                )
                assert sid2

                ws2 = await websockets.connect(
                    f'ws://127.0.0.1:{pp}/client-persist2-raw',
                    max_size=50 * 1024 * 1024,
                )
                resp = await send_cdp(
                    ws2,
                    'Network.getCookies',
                    {'urls': ['https://example.com']},
                    session_id=sid2,
                )
                await ws2.close()

                cookies = resp.get('result', {}).get('cookies', [])
                found = [c for c in cookies if c['name'] == 'persist_test']
                assert len(found) == 1, (
                    f'Cookie not found after proxy restart. '
                    f'Cookies: {[c["name"] for c in cookies]}'
                )
                assert found[0]['value'] == 'survived'
            finally:
                await kill(s2)
        finally:
            await new_proxy.stop()
            # Replace proxy ref so fixture cleanup doesn't double-stop
            env['proxy'] = new_proxy
