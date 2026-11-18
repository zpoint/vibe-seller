"""Shared engine for inject-driven anti-detect backends (Route B).

A concrete backend supplies an :class:`InjectClient` factory; this engine
turns it into a :class:`BrowserBackend`: ensure the client is up and
logged in, open the target store with a debug port injected, verify the
CDP endpoint, then front it with a ``CDPMuxProxy`` so multiple
browser-use CLIs share one logged-in env — identical to the ziniao /
chrome path from the CDP endpoint onward.
"""

import asyncio
from collections.abc import Callable
import logging

import aiohttp

from app.browser.base import BrowserBackend, BrowserSessionInfo
from app.browser.cdp_mux_proxy import CDPMuxProxy
from app.browser.downloads import prepare_download_dir
from app.browser.inject.client import InjectClient
from app.browser.inject.errors import CdpUnreachable, LoginRequired
from app.config import LOCALHOST

logger = logging.getLogger(__name__)

ClientFactory = Callable[[dict], InjectClient]


class InjectCdpBackend(BrowserBackend):
    """Route-B backend base: GUI client + injected CDP → CDPMuxProxy."""

    # Overridable so tests can make the reachability probe cheap.
    _CDP_ATTEMPTS = 4
    _CDP_DELAY = 1.5

    def __init__(self, client_factory: ClientFactory):
        self._client_factory = client_factory
        self._client: InjectClient | None = None
        self._proxy: CDPMuxProxy | None = None
        self._store_ref: str | None = None

    async def start(self, browser_config: dict) -> BrowserSessionInfo:
        store_ref = browser_config.get('browser_ref', '')
        proxy_port = int(browser_config.get('proxy_port', 9222))
        slug = browser_config.get('store_slug', 'default')

        client = self._client_factory(browser_config)
        self._client = client

        await client.ensure_running()
        if not await client.is_logged_in():
            raise LoginRequired()

        opened = await client.open_store(store_ref)
        self._store_ref = store_ref

        if not await self._cdp_reachable(opened.cdp_host, opened.cdp_port):
            raise CdpUnreachable(
                f'opened {store_ref} but CDP {opened.cdp_host}:'
                f'{opened.cdp_port} never answered'
            )

        dl_dir = await asyncio.to_thread(prepare_download_dir, slug)
        self._proxy = CDPMuxProxy(
            listen_port=proxy_port,
            target_port=opened.cdp_port,
            target_host=opened.cdp_host,
            download_dir=str(dl_dir),
            keep_last_page=True,
        )
        await self._proxy.start()
        logger.info(
            'Inject backend CDPMuxProxy ready: %s:%d -> %s:%d (store %s)',
            LOCALHOST,
            proxy_port,
            opened.cdp_host,
            opened.cdp_port,
            store_ref,
        )
        return BrowserSessionInfo(cdp_port=opened.cdp_port)

    async def stop(self, info: BrowserSessionInfo) -> None:
        try:
            if self._proxy:
                await self._proxy.stop()
                self._proxy = None
        except Exception as e:
            logger.warning('inject backend proxy stop failed: %s', e)
        if self._client is not None and self._store_ref is not None:
            try:
                await self._client.close_store(self._store_ref)
            except Exception as e:
                logger.warning('inject backend close_store failed: %s', e)
            finally:
                self._store_ref = None

    async def _cdp_reachable(self, host: str, port: int) -> bool:
        """True once Chrome's CDP endpoint answers on ``host:port``."""
        url = f'http://{host}:{port}/json/version'
        for i in range(self._CDP_ATTEMPTS):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        data = await resp.json()
                        if data.get('webSocketDebuggerUrl'):
                            return True
            except Exception:
                pass
            if i < self._CDP_ATTEMPTS - 1:
                await asyncio.sleep(self._CDP_DELAY)
        return False
