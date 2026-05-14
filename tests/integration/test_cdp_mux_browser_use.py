"""
Integration tests: CDPMuxProxy + real browser-use BrowserSession.

All tests use real browser-use BrowserSession through the proxy.
Chrome launched once per module (_browser fixture); proxy is
per-test (function-scoped) for clean state isolation.
"""

import asyncio
import json
import socket
import time

import aiohttp
from browser_use import BrowserSession
import pytest
import websockets

from app.browser.cdp_mux_proxy import CDPMuxProxy
from tests.integration.conftest import cleanup_browser_tabs

SESSION_TIMEOUT = 15


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

COUNTER_HTML = (
    'data:text/html,'
    '<div id="val">0</div>'
    '<script>'
    'let c=0;'
    'setInterval(()=>document.getElementById("val").textContent=++c,100);'
    'window.__CLIENT="{client}";'
    '</script>'
)


async def make_session(pp, client_id):
    """Create+start a BrowserSession through the proxy."""
    s = BrowserSession(cdp_url=f'ws://127.0.0.1:{pp}/client-{client_id}')
    await asyncio.wait_for(s.start(), timeout=SESSION_TIMEOUT)
    return s


async def kill(session):
    """Kill session, suppress errors, don't wait long."""
    try:
        await asyncio.wait_for(session.kill(), timeout=2)
    except Exception:
        pass


async def kill_all(sessions):
    await asyncio.gather(*[kill(s) for s in sessions])


async def connect_direct(port):
    """Raw CDP to Chrome (bypass proxy)."""
    async with aiohttp.ClientSession() as s:
        async with s.get(f'http://127.0.0.1:{port}/json/version') as r:
            data = await r.json()
    return await websockets.connect(
        data['webSocketDebuggerUrl'], max_size=50 * 1024 * 1024
    )


_cdp_counter_lock = asyncio.Lock()
_cdp_counter = 0


async def send_cdp(ws, method, params=None, session_id=None):
    """Send CDP command with a unique ID, wait for matching response."""
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


async def direct_page_count(bp):
    """Count page targets via direct CDP."""
    d = await connect_direct(bp)
    resp = await send_cdp(d, 'Target.getTargets')
    targets = [
        t
        for t in resp.get('result', {}).get('targetInfos', [])
        if t.get('type') == 'page'
    ]
    await d.close()
    return len(targets)


# ------------------------------------------------------------------
# Fixture
# ------------------------------------------------------------------


@pytest.fixture
async def env(_browser):
    bp = _browser
    pp = _free_port()
    proxy = CDPMuxProxy(
        listen_port=pp, target_port=bp, max_clients=10, cleanup_grace=0
    )
    await proxy.start()
    yield {'proxy': proxy, 'pp': pp, 'bp': bp}
    await proxy.stop()
    await cleanup_browser_tabs(bp)


# ==================================================================
# Group 1: Basic isolation
# ==================================================================


@pytest.mark.integration
class TestBasicIsolation:
    @pytest.mark.asyncio
    async def test_two_sessions_render_independently(self, env):
        """Two sessions with dynamic JS counters, each isolated."""
        sa = await make_session(env['pp'], 'a')
        sb = await make_session(env['pp'], 'b')
        try:
            pa = await sa.get_current_page()
            pb = await sb.get_current_page()
            await pa.goto(COUNTER_HTML.format(client='A'))
            await pb.goto(COUNTER_HTML.format(client='B'))
            await asyncio.sleep(0.5)

            ca = await pa.evaluate(
                '() => document.getElementById("val").textContent'
            )
            cb = await pb.evaluate(
                '() => document.getElementById("val").textContent'
            )
            assert int(ca) > 0
            assert int(cb) > 0
            assert await pa.evaluate('() => window.__CLIENT') == 'A'
            assert await pb.evaluate('() => window.__CLIENT') == 'B'
        finally:
            await kill_all([sa, sb])

    @pytest.mark.asyncio
    async def test_five_simultaneous_start(self, env):
        """5 BrowserSession.start() concurrently — no race."""
        sessions = await asyncio.gather(
            *[make_session(env['pp'], f's5-{i}') for i in range(5)],
            return_exceptions=True,
        )
        started = [s for s in sessions if isinstance(s, BrowserSession)]
        try:
            assert len(started) == 5, (
                f'Failures: {[s for s in sessions if isinstance(s, Exception)]}'
            )
            assert len(env['proxy']._clients) == 5
            # Each has a page
            for s in started:
                p = await s.get_current_page()
                assert p is not None
        finally:
            await kill_all(started)

    @pytest.mark.asyncio
    async def test_get_targets_filtered_per_client(self, env):
        """Each session sees only its own pages via Target.getTargets."""
        pp, bp = env['pp'], env['bp']
        sa = await make_session(pp, 'ft-a')
        sb = await make_session(pp, 'ft-b')
        try:
            pa = await sa.get_current_page()
            pb = await sb.get_current_page()
            await pa.goto('data:text/html,<h1>A</h1>')
            await pb.goto('data:text/html,<h1>B</h1>')

            # get_page_targets() calls Target.getTargets through
            # each session's proxy connection — verifies filtering.
            targets_a = sa.get_page_targets()
            targets_b = sb.get_page_targets()
            assert len(targets_a) >= 1
            assert len(targets_b) >= 1
            ids_a = {t.target_id for t in targets_a}
            ids_b = {t.target_id for t in targets_b}
            assert ids_a.isdisjoint(ids_b), (
                f'Targets overlap: A={ids_a}, B={ids_b}'
            )

            # Direct sees both
            count = await direct_page_count(bp)
            assert count >= 2
        finally:
            await kill_all([sa, sb])


# ==================================================================
# Group 2: Isolation enforcement
# ==================================================================


@pytest.mark.integration
class TestIsolationEnforcement:
    @pytest.mark.asyncio
    async def test_cannot_attach_or_close_other_tabs(self, env):
        """Cross-client attach/close blocked by proxy."""
        pp = env['pp']
        sa = await make_session(pp, 'enf-a')
        sb = await make_session(pp, 'enf-b')
        try:
            # Get B's targetId from proxy internals
            b_targets = (
                list(
                    env['proxy']
                    ._clients.get(
                        'enf-b', type('', (), {'target_ids': set()})()
                    )
                    .target_ids
                )
                if 'enf-b' in env['proxy']._clients
                else []
            )
            assert b_targets, 'B has no targets'
            b_tid = b_targets[0]

            # A tries via raw CDP through proxy (separate client ID)
            ws = await websockets.connect(
                f'ws://127.0.0.1:{pp}/client-enf-a-raw',
                max_size=50 * 1024 * 1024,
            )
            r = await send_cdp(
                ws,
                'Target.attachToTarget',
                {'targetId': b_tid, 'flatten': True},
            )
            assert 'error' in r
            r = await send_cdp(ws, 'Target.closeTarget', {'targetId': b_tid})
            assert 'error' in r
            await ws.close()
        finally:
            await kill_all([sa, sb])

    @pytest.mark.asyncio
    async def test_unowned_targets_invisible(self, env):
        """Chrome's initial about:blank hidden from clients."""
        pp = env['pp']
        ws = await websockets.connect(
            f'ws://127.0.0.1:{pp}/client-unowned',
            max_size=50 * 1024 * 1024,
        )
        resp = await send_cdp(ws, 'Target.getTargets')
        targets = [
            t
            for t in resp.get('result', {}).get('targetInfos', [])
            if t.get('type') == 'page'
        ]
        await ws.close()
        assert len(targets) == 0


# ==================================================================
# Group 3: Cleanup & resilience
# ==================================================================


@pytest.mark.integration
class TestCleanup:
    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self, env):
        """Session kill → tabs closed, proxy maps clean."""
        pp = env['pp']
        proxy = env['proxy']
        s = await make_session(pp, 'dc')
        p = await s.get_current_page()
        await p.goto('data:text/html,<h1>cleanup</h1>')
        assert len(proxy._clients) == 1
        await kill(s)
        await asyncio.sleep(0.5)
        assert len(proxy._clients) == 0
        assert len(proxy._target_to_client) == 0

    @pytest.mark.asyncio
    async def test_crash_doesnt_affect_others(self, env):
        """Kill one session's WebSocket, others survive."""
        pp = env['pp']
        sa = await make_session(pp, 'cra')
        sb = await make_session(pp, 'crb')
        sc = await make_session(pp, 'crc')
        try:
            pa = await sa.get_current_page()
            pc = await sc.get_current_page()
            await pa.goto(COUNTER_HTML.format(client='A'))
            await pc.goto(COUNTER_HTML.format(client='C'))

            # Record B's original target
            b_original_target = list(env['proxy']._clients['crb'].target_ids)[0]

            # Force-disconnect B via proxy (simulates crash)
            await env['proxy'].disconnect_client('crb')
            await asyncio.sleep(1)

            # B's original target was cleaned up (even if
            # browser-use reconnected and created a new one)
            assert b_original_target not in env['proxy']._target_to_client
            ca = await pa.evaluate(
                '() => document.getElementById("val").textContent'
            )
            cc = await pc.evaluate(
                '() => document.getElementById("val").textContent'
            )
            assert int(ca) > 0
            assert int(cc) > 0
        finally:
            await kill_all([sa, sb, sc])

    @pytest.mark.asyncio
    async def test_sequential_connect_disconnect_no_leak(self, env):
        """3 cycles of connect→use→disconnect, no leaks."""
        pp = env['pp']
        proxy = env['proxy']
        for i in range(3):
            s = await make_session(pp, f'lk{i}')
            p = await s.get_current_page()
            await p.goto(f'data:text/html,<h1>{i}</h1>')
            await kill(s)
            await asyncio.sleep(0.5)
        assert len(proxy._clients) == 0
        assert len(proxy._target_to_client) == 0
        assert len(proxy._session_to_client) == 0


# ==================================================================
# Group 4: Shared state
# ==================================================================


@pytest.mark.integration
class TestSharedState:
    @pytest.mark.asyncio
    async def test_cookies_shared_across_clients(self, env):
        """Cookie set by A visible to B (shared default context)."""
        pp = env['pp']
        sa = await make_session(pp, 'cka')
        sb = await make_session(pp, 'ckb')
        try:
            # Get session IDs from proxy
            sid_a = next(
                (
                    s
                    for s, c in env['proxy']._session_to_client.items()
                    if c == 'cka'
                ),
                None,
            )
            sid_b = next(
                (
                    s
                    for s, c in env['proxy']._session_to_client.items()
                    if c == 'ckb'
                ),
                None,
            )
            assert sid_a and sid_b

            # Set cookie via A's raw CDP
            ws_a = await websockets.connect(
                f'ws://127.0.0.1:{pp}/client-cka-raw',
                max_size=50 * 1024 * 1024,
            )
            await send_cdp(
                ws_a,
                'Network.setCookie',
                {
                    'name': 'shared',
                    'value': 'yes',
                    'domain': 'example.com',
                    'path': '/',
                },
                session_id=sid_a,
            )
            await ws_a.close()

            # Read via B's raw CDP
            ws_b = await websockets.connect(
                f'ws://127.0.0.1:{pp}/client-ckb-raw',
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
            found = [c for c in cookies if c['name'] == 'shared']
            assert len(found) == 1
            assert found[0]['value'] == 'yes'
        finally:
            await kill_all([sa, sb])


# ==================================================================
# Group 5: Stress & event flooding
# ==================================================================


@pytest.mark.integration
class TestStress:
    @pytest.mark.asyncio
    async def test_discover_targets_flood(self, env):
        """30 setDiscoverTargets from 3 clients don't break proxy."""
        pp = env['pp']
        proxy = env['proxy']
        sessions = await asyncio.gather(*[
            make_session(pp, f'df{i}') for i in range(3)
        ])
        try:
            # Navigate so each has a page
            for s in sessions:
                p = await s.get_current_page()
                await p.goto('data:text/html,<h1>flood</h1>')

            # Flood setDiscoverTargets via separate raw WS clients
            async def flood(cid):
                ws = await websockets.connect(
                    f'ws://127.0.0.1:{pp}/client-{cid}-raw',
                    max_size=50 * 1024 * 1024,
                )
                for _ in range(10):
                    await send_cdp(
                        ws,
                        'Target.setDiscoverTargets',
                        {'discover': True},
                    )
                await ws.close()

            await asyncio.gather(*[flood(f'df{i}') for i in range(3)])

            # Sessions still work
            for s in sessions:
                p = await s.get_current_page()
                v = await p.evaluate('() => 1+1')
                assert str(v) == '2'

            assert len(proxy._pending_attached) < 50
            assert len(proxy._pending_created) < 50
        finally:
            await kill_all(sessions)

    @pytest.mark.asyncio
    async def test_rapid_create_close_cycles(self, env):
        """3 sessions × 10 create→close via raw CDP."""
        pp = env['pp']
        sessions = await asyncio.gather(*[
            make_session(pp, f'rc{i}') for i in range(3)
        ])
        try:

            async def cycle(cid):
                ws = await websockets.connect(
                    f'ws://127.0.0.1:{pp}/client-{cid}-raw',
                    max_size=50 * 1024 * 1024,
                )
                for _ in range(10):
                    resp = await send_cdp(
                        ws,
                        'Target.createTarget',
                        {'url': 'about:blank'},
                    )
                    tid = resp.get('result', {}).get('targetId')
                    if tid:
                        await send_cdp(
                            ws,
                            'Target.closeTarget',
                            {'targetId': tid},
                        )
                await ws.close()

            await asyncio.gather(*[cycle(f'rc{i}') for i in range(3)])

            # Sessions still alive
            for s in sessions:
                p = await s.get_current_page()
                assert p is not None
        finally:
            await kill_all(sessions)

    @pytest.mark.asyncio
    async def test_reconnect_storm(self, env):
        """5 connect+disconnect, then 5 reconnect."""
        pp = env['pp']
        proxy = env['proxy']
        # Round 1
        r1 = await asyncio.gather(*[
            make_session(pp, f'rs1-{i}') for i in range(5)
        ])
        await kill_all(r1)
        await asyncio.sleep(0.5)
        for i in range(5):
            assert f'rs1-{i}' not in proxy._clients

        # Round 2
        r2 = await asyncio.gather(*[
            make_session(pp, f'rs2-{i}') for i in range(5)
        ])
        try:
            assert len(proxy._clients) == 5
            for s in r2:
                p = await s.get_current_page()
                await p.goto('data:text/html,<h1>r2</h1>')
                await asyncio.sleep(0.1)
                v = await p.evaluate(
                    '() => document.querySelector("h1").textContent'
                )
                assert v == 'r2'
        finally:
            await kill_all(r2)

    @pytest.mark.asyncio
    async def test_high_freq_message_interleaving(self, env):
        """3 sessions × 50 evaluate — correct routing."""
        pp = env['pp']
        sessions = await asyncio.gather(*[
            make_session(pp, f'hf{i}') for i in range(3)
        ])
        try:
            for s in sessions:
                p = await s.get_current_page()
                await p.goto('data:text/html,<h1>HF</h1>')

            # Get session IDs
            sids = []
            for i in range(3):
                sid = next(
                    (
                        s
                        for s, c in env['proxy']._session_to_client.items()
                        if c == f'hf{i}'
                    ),
                    None,
                )
                sids.append(sid)

            async def batch(cid, sid, val):
                ws = await websockets.connect(
                    f'ws://127.0.0.1:{pp}/client-{cid}-raw',
                    max_size=50 * 1024 * 1024,
                )
                results = []
                for _ in range(50):
                    r = await send_cdp(
                        ws,
                        'Runtime.evaluate',
                        {'expression': str(val)},
                        session_id=sid,
                    )
                    results.append(
                        r.get('result', {}).get('result', {}).get('value')
                    )
                await ws.close()
                return results

            r0, r1, r2 = await asyncio.gather(
                batch('hf0', sids[0], 100),
                batch('hf1', sids[1], 200),
                batch('hf2', sids[2], 300),
            )
            assert all(v == 100 for v in r0)
            assert all(v == 200 for v in r1)
            assert all(v == 300 for v in r2)
        finally:
            await kill_all(sessions)

    @pytest.mark.asyncio
    async def test_concurrent_5_client_page_creation(self, env):
        """5 sessions each create 2 pages simultaneously."""
        pp, bp = env['pp'], env['bp']
        proxy = env['proxy']
        sessions = await asyncio.gather(*[
            make_session(pp, f'cp{i}') for i in range(5)
        ])
        try:

            async def make_extra_page(cid):
                ws = await websockets.connect(
                    f'ws://127.0.0.1:{pp}/client-{cid}-raw',
                    max_size=50 * 1024 * 1024,
                )
                await send_cdp(
                    ws,
                    'Target.createTarget',
                    {'url': 'about:blank'},
                )
                await ws.close()

            await asyncio.gather(*[make_extra_page(f'cp{i}') for i in range(5)])

            await asyncio.sleep(0.3)  # Let raw clients finish

            # 5 browser-use clients remain (raw clients disconnected)
            # But total pages created: 10 (5 initial + 5 extra)
            assert len(proxy._clients) >= 5

            # Direct sees browser-use pages + raw pages may have
            # been cleaned up when raw WS closed. At minimum, the
            # 5 browser-use sessions' pages exist.
            count = await direct_page_count(bp)
            assert count >= 5

            # Proxy maps: no cross-contamination
            for cid, client in proxy._clients.items():
                for tid in client.target_ids:
                    assert proxy._target_to_client[tid] == cid
        finally:
            await kill_all(sessions)


# ==================================================================
# Group 6: State consistency
# ==================================================================


@pytest.mark.integration
class TestStateConsistency:
    @pytest.mark.asyncio
    async def test_proxy_invariants(self, env):
        """Proxy maps consistent after operations."""
        pp = env['pp']
        proxy = env['proxy']

        sessions = await asyncio.gather(*[
            make_session(pp, f'iv{i}') for i in range(3)
        ])
        for s in sessions:
            p = await s.get_current_page()
            await p.goto('data:text/html,<h1>inv</h1>')

        # Kill first two
        await kill(sessions[0])
        await kill(sessions[1])
        await asyncio.sleep(0.5)

        try:
            for tid, cid in proxy._target_to_client.items():
                assert cid in proxy._clients
            for sid, cid in proxy._session_to_client.items():
                assert cid in proxy._clients
            now = time.monotonic()
            for _, (ts, __) in proxy._pending_attached.items():
                assert now - ts < 30
            for _, (ts, __) in proxy._pending_created.items():
                assert now - ts < 30
            assert 'iv0' not in proxy._clients
            assert 'iv1' not in proxy._clients
            assert 'iv2' in proxy._clients
        finally:
            await kill(sessions[2])
