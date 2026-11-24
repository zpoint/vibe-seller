"""Integration: how ``ChromeBackend`` launches a store browser.

On a real Chromium launched by ``ChromeBackend`` (headless — what CI can
run), these pin what a store browser must do:

* Look like a person's browser window to a page. Platform risk control
  (Taobao's 生意参谋 "abnormal access" prompt, Amazon's re-sign-in)
  judges a session by what a page can read: ``navigator.webdriver``,
  and a page size that follows the window.
* Keep SESSION cookies across a restart in headless mode too —
  Taobao-style logins live in session cookies.
* Send its traffic through the store's own proxy (``proxy_server`` /
  ``proxy_bypass`` from the store form), credentials included.
"""

import asyncio
import base64
from contextlib import asynccontextmanager
import json
import shutil
import socket
import urllib.parse
import uuid

import aiohttp
import pytest
import websockets

from app.browser.chrome import PROFILES_DIR, ChromeBackend

# Hosts that exist nowhere in DNS: only a proxy can answer for them.
PROXY_ONLY_HOST = 'vs-proxy-only.test'
BYPASSED_HOST = 'vs-bypassed.test'


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class _Cdp:
    """Minimal browser-level CDP client on the backend's debug port."""

    def __init__(self, ws):
        self._ws = ws
        self._next_id = 0

    @classmethod
    @asynccontextmanager
    async def connect(cls, debug_port: int):
        url = f'http://127.0.0.1:{debug_port}/json/version'
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                ws_url = (await resp.json())['webSocketDebuggerUrl']
        async with websockets.connect(ws_url, max_size=2**25) as ws:
            yield cls(ws)

    async def send(self, method, params=None, session_id=None) -> dict:
        self._next_id += 1
        msg = {'id': self._next_id, 'method': method, 'params': params or {}}
        if session_id:
            msg['sessionId'] = session_id
        await self._ws.send(json.dumps(msg))
        while True:
            resp = json.loads(await asyncio.wait_for(self._ws.recv(), 20))
            if resp.get('id') == self._next_id:
                assert 'error' not in resp, resp
                return resp['result']

    async def new_page(self) -> tuple[str, str]:
        """Open a tab; return (target_id, session_id)."""
        target = await self.send('Target.createTarget', {'url': 'about:blank'})
        attached = await self.send(
            'Target.attachToTarget',
            {'targetId': target['targetId'], 'flatten': True},
        )
        return target['targetId'], attached['sessionId']

    async def eval(self, session_id: str, expression: str):
        result = await self.send(
            'Runtime.evaluate',
            {'expression': expression, 'returnByValue': True},
            session_id=session_id,
        )
        return result['result'].get('value')

    async def open(self, session_id: str, url: str) -> str:
        """Navigate; return the page's body text ('' when it failed)."""
        nav = await self.send('Page.navigate', {'url': url}, session_id)
        if nav.get('errorText'):
            return ''
        for _ in range(100):
            text = await self.eval(
                session_id,
                "document.readyState === 'complete' && location.href !== "
                "'about:blank' ? document.body.innerText : null",
            )
            if text is not None:
                return text
            await asyncio.sleep(0.1)
        return ''


@asynccontextmanager
async def _store_browser(slug: str, **store_config):
    """A headless store browser, started the way the manager starts one
    (``store_config`` is the store's own browser_config)."""
    backend = ChromeBackend()
    info = await backend.start({
        **store_config,
        'proxy_port': _free_port(),
        'headless': True,
        'store_slug': slug,
    })
    try:
        yield info
    finally:
        await backend.stop(info)


class _AnsweringProxy:
    """A forward HTTP proxy that answers every request itself.

    It records each requested host and replies ``via-proxy``, so a page
    that shows ``via-proxy`` for a host absent from DNS can only have
    come through it. With ``credentials`` it first demands Basic auth.
    """

    def __init__(self, credentials: tuple[str, str] | None = None):
        self.hosts: list[str] = []
        self._auth = None
        if credentials:
            token = base64.b64encode(':'.join(credentials).encode()).decode()
            self._auth = f'Basic {token}'
        self._server = None
        self.port = 0

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._handle, '127.0.0.1', 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            while True:
                head = (await reader.readuntil(b'\r\n\r\n')).decode('latin-1')
                lines = head.split('\r\n')
                target = lines[0].split(' ')[1]
                headers = {
                    k.lower(): v
                    for k, _, v in (ln.partition(': ') for ln in lines[1:])
                }
                if self._auth and headers.get('proxy-authorization') != (
                    self._auth
                ):
                    writer.write(
                        b'HTTP/1.1 407 Proxy Authentication Required\r\n'
                        b'Proxy-Authenticate: Basic realm="store"\r\n'
                        b'Content-Length: 0\r\n\r\n'
                    )
                    await writer.drain()
                    continue
                self.hosts.append(urllib.parse.urlsplit(target).hostname)
                body = b'<html><body>via-proxy</body></html>'
                writer.write(
                    b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n'
                    b'Content-Length: %d\r\n\r\n' % len(body) + body
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


@pytest.fixture
def slug():
    name = f'test-launch-{uuid.uuid4().hex[:8]}'
    yield name
    shutil.rmtree(PROFILES_DIR / name, ignore_errors=True)


@pytest.mark.integration
class TestChromeStoreFingerprint:
    async def test_page_is_not_told_it_is_automated(self, slug):
        async with _store_browser(slug) as info:
            async with _Cdp.connect(info.cdp_port) as cdp:
                _, session = await cdp.new_page()
                assert await cdp.eval(session, 'navigator.webdriver') is False

    async def test_page_fills_the_window(self, slug):
        """Resizing the window resizes the page — no emulated viewport
        pinning every page to 1280x720 behind the window's back."""
        async with _store_browser(slug) as info:
            async with _Cdp.connect(info.cdp_port) as cdp:
                target, session = await cdp.new_page()
                window = await cdp.send(
                    'Browser.getWindowForTarget', {'targetId': target}
                )
                await cdp.send(
                    'Browser.setWindowBounds',
                    {
                        'windowId': window['windowId'],
                        'bounds': {'width': 1000, 'height': 700},
                    },
                )
                width = None
                for _ in range(50):
                    width = await cdp.eval(session, 'window.innerWidth')
                    if width != 1280:
                        break
                    await asyncio.sleep(0.1)
                assert abs(width - 1000) <= 16, width


@pytest.mark.integration
class TestChromeStoreSessionCookies:
    async def test_session_cookie_survives_headless_restart(self, slug):
        """chromium-headless-shell drops session cookies at startup; the
        store browser must keep them, as it does with a window."""
        async with _store_browser(slug) as info:
            async with _Cdp.connect(info.cdp_port) as cdp:
                _, session = await cdp.new_page()
                await cdp.send(
                    'Network.setCookie',
                    {
                        # No `expires`: a session cookie, like Taobao's
                        # cookie2 / _tb_token_ / unb.
                        'name': 'login',
                        'value': 'kept',
                        'url': 'http://127.0.0.1/',
                    },
                    session_id=session,
                )

        async with _store_browser(slug) as info:
            async with _Cdp.connect(info.cdp_port) as cdp:
                _, session = await cdp.new_page()
                result = await cdp.send(
                    'Network.getCookies',
                    {'urls': ['http://127.0.0.1/']},
                    session_id=session,
                )
        cookies = {c['name']: c['value'] for c in result['cookies']}
        assert cookies.get('login') == 'kept', cookies


@pytest.mark.integration
class TestChromeStoreProxy:
    async def test_pages_go_through_the_store_proxy(self, slug):
        async with _AnsweringProxy() as proxy:
            config = {'proxy_server': f'http://127.0.0.1:{proxy.port}'}
            async with _store_browser(slug, **config) as info:
                async with _Cdp.connect(info.cdp_port) as cdp:
                    _, session = await cdp.new_page()
                    text = await cdp.open(session, f'http://{PROXY_ONLY_HOST}/')
        assert text == 'via-proxy'
        assert PROXY_ONLY_HOST in proxy.hosts

    async def test_bypass_hosts_go_direct(self, slug):
        async with _AnsweringProxy() as proxy:
            config = {
                'proxy_server': f'http://127.0.0.1:{proxy.port}',
                'proxy_bypass': BYPASSED_HOST,
            }
            async with _store_browser(slug, **config) as info:
                async with _Cdp.connect(info.cdp_port) as cdp:
                    _, session = await cdp.new_page()
                    bypassed = await cdp.open(
                        session, f'http://{BYPASSED_HOST}/'
                    )
                    proxied = await cdp.open(
                        session, f'http://{PROXY_ONLY_HOST}/'
                    )
        assert bypassed != 'via-proxy'
        assert BYPASSED_HOST not in proxy.hosts
        assert proxied == 'via-proxy'

    async def test_credentials_in_the_proxy_url_are_used(self, slug):
        """`http://user:pass@host:port` — Chromium rejects credentials in
        the server URL, so they must answer the proxy's auth challenge."""
        async with _AnsweringProxy(credentials=('vs-user', 'p@ss')) as proxy:
            config = {
                'proxy_server': f'http://vs-user:p%40ss@127.0.0.1:{proxy.port}'
            }
            async with _store_browser(slug, **config) as info:
                async with _Cdp.connect(info.cdp_port) as cdp:
                    _, session = await cdp.new_page()
                    text = await cdp.open(session, f'http://{PROXY_ONLY_HOST}/')
        assert text == 'via-proxy'
        assert PROXY_ONLY_HOST in proxy.hosts
