"""Shared engine for inject-driven anti-detect backends (Route B).

A concrete backend supplies an :class:`InjectClient` factory; this engine
turns it into a :class:`BrowserBackend`: ensure the client is up and
logged in, open the target store with a debug port injected, verify the
CDP endpoint, then front it with a ``CDPMuxProxy`` so multiple
browser-use CLIs share one logged-in env — identical to the
HTTP-driven / chrome path from the CDP endpoint onward.
"""

import asyncio
from collections.abc import Callable
import logging

import aiohttp

from app.browser.base import BrowserBackend, BrowserSessionInfo
from app.browser.cdp_mux_proxy import CDPMuxProxy
from app.browser.downloads import prepare_download_dir
from app.browser.inject.client import InjectClient, OpenedStore
from app.browser.inject.errors import (
    CdpUnreachable,
    LoginRequired,
    UnsupportedKernel,
)
from app.config import LOCALHOST

logger = logging.getLogger(__name__)

ClientFactory = Callable[[dict], InjectClient]

# The Browser families the engine can drive: Chromium's CDP. Firefox
# kernels can also expose a PARTIAL CDP (the old Remote Agent — seen
# live: a Firefox/100 kernel answered /json/version with a
# webSocketDebuggerUrl), which the mux proxy would happily front while
# every real command fails. Keep in ONE place.
CHROMIUM_BROWSER_PREFIXES = (
    'Chrome/',
    'HeadlessChrome/',
    'Chromium/',
    'Edg/',
)


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
        self._opened: OpenedStore | None = None

    async def start(self, browser_config: dict) -> BrowserSessionInfo:
        store_ref = browser_config.get('browser_ref', '')
        proxy_port = int(browser_config.get('proxy_port', 9222))
        slug = browser_config.get('store_slug', 'default')

        client = self._client_factory(browser_config)
        self._client = client

        await client.ensure_running()
        # Auto-login if logged out and credentials are stored (no-op for a
        # client whose login is managed elsewhere). Raises LoginRequired when
        # it cannot log in, so a logged-out store fails fast and clearly
        # instead of the agent flailing against a login wall.
        await client.ensure_logged_in()
        if not await client.is_logged_in():
            raise LoginRequired()

        opened = await client.open_store(store_ref)
        self._store_ref = store_ref
        self._opened = opened

        # The open has created a real env (windows, processes) that
        # nothing else will ever close: EVERY post-open failure funnels
        # through one best-effort cleanup before its own error
        # propagates. Seen live pre-funnel: a Firefox/BiDi env whose CDP
        # never answered outlived CdpUnreachable with 11 processes.
        try:
            version = await self._cdp_version(opened.cdp_host, opened.cdp_port)
            if version is None:
                raise CdpUnreachable(
                    f'opened {store_ref} but CDP {opened.cdp_host}:'
                    f'{opened.cdp_port} never answered'
                )
            browser = str(version.get('Browser') or '')
            if not browser.startswith(CHROMIUM_BROWSER_PREFIXES):
                # A non-Chromium kernel CAN expose a partial CDP
                # (Firefox's old Remote Agent) — reachable is not
                # drivable. Refuse before after_open or the proxy ever
                # front it; the funnel below closes the env.
                logger.warning(
                    'store %s: CDP endpoint reports Browser %r (not '
                    'Chromium-family); closing the env and refusing',
                    store_ref,
                    browser,
                )
                raise UnsupportedKernel(
                    f"this store's browser kernel reports {browser!r} "
                    'via CDP; only Chromium-family kernels are '
                    'supported (expected one of '
                    f'{CHROMIUM_BROWSER_PREFIXES})'
                )

            # A client-specific entry step (an intermediate store-info
            # page) must run
            # BEFORE the proxy: the proxy's startup cleanup closes
            # orphan web tabs, so the store page this produces has to
            # exist first (the engine's keep_last_page keeps it).
            await client.after_open(opened)

            dl_dir = await asyncio.to_thread(prepare_download_dir, slug)
            self._proxy = CDPMuxProxy(
                listen_port=proxy_port,
                target_port=opened.cdp_port,
                target_host=opened.cdp_host,
                download_dir=str(dl_dir),
                keep_last_page=True,
                instance_id=browser_config.get('vibe_instance_id'),
                listen_sock=browser_config.get('proxy_socket'),
            )
            await self._proxy.start()
        except BaseException:
            # Best-effort, never masking: the ORIGINAL error (the
            # CdpUnreachable / UnsupportedKernel / vendor failure that
            # tripped the funnel) is re-raised after the cleanup.
            # BaseException, not Exception: a CANCELLED start (a task
            # stop or a server shutdown mid-start) leaks the env just
            # the same, so it funnels too — and a second cancel during
            # the cleanup simply ends it.
            await self._abandon_open_env()
            raise

        logger.info(
            'Inject backend CDPMuxProxy ready: %s:%d -> %s:%d (store %s, '
            'instance %s)',
            LOCALHOST,
            proxy_port,
            opened.cdp_host,
            opened.cdp_port,
            store_ref,
            self._proxy.instance_id or '<none>',
        )
        return BrowserSessionInfo(cdp_port=opened.cdp_port)

    async def stop(self, info: BrowserSessionInfo) -> None:
        # Close the store env itself (CDP Browser.close) BEFORE tearing
        # down the proxy, so the kernel exits cleanly and the next task
        # re-opens a fresh env. Without this, close_store is a no-op and a
        # second task on the same store finds it already open (no new
        # spawn → CdpUnreachable).
        if self._opened is not None:
            await self._close_browser(
                self._opened.cdp_host, self._opened.cdp_port
            )
            self._opened = None
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

    async def _abandon_open_env(self) -> None:
        """Best-effort teardown of the env a FAILED start leaves behind.

        Every post-open failure on the start path funnels here: the env
        the open created (windows, processes) must not outlive the error
        (seen live: a Firefox/BiDi env survived ``CdpUnreachable`` with
        11 processes). A CANCELLED start reaches here too — the funnel
        catches BaseException. ``Browser.close`` runs FIRST so the
        kernel exits
        cleanly and the vendor's ``close_store`` reopens a fresh env on
        the next task; a half-started proxy is stopped too. Each step is
        guarded and logged — cleanup must never mask the original error
        the caller re-raises. (The stop path stays separate: it owns a
        SUCCESSFUL session's teardown.)
        """
        if self._opened is not None:
            await self._close_browser(
                self._opened.cdp_host, self._opened.cdp_port
            )
            self._opened = None
        if self._proxy is not None:
            try:
                await self._proxy.stop()
            except Exception as e:
                logger.warning('inject backend proxy stop failed: %s', e)
            self._proxy = None
        if self._client is not None and self._store_ref is not None:
            try:
                await self._client.close_store(self._store_ref)
            except Exception as e:
                logger.warning(
                    'inject backend close_store cleanup failed: %s',
                    e,
                )
            finally:
                self._store_ref = None

    @staticmethod
    async def _close_browser(host: str, port: int) -> None:
        """Best-effort CDP ``Browser.close`` on the kernel (clean exit)."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f'http://{host}:{port}/json/version',
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    ws_url = (await resp.json()).get('webSocketDebuggerUrl')
                if not ws_url:
                    return
                async with session.ws_connect(ws_url, timeout=10) as ws:
                    await ws.send_json({'id': 1, 'method': 'Browser.close'})
                    try:
                        await asyncio.wait_for(ws.receive(), timeout=3)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug('Browser.close on %s:%s failed: %s', host, port, e)

    async def _cdp_version(self, host: str, port: int) -> dict | None:
        """``/json/version`` once it answers, else None.

        The dict carries the ``Browser`` family — the caller refuses a
        non-Chromium kernel (a partial CDP is reachable but not
        drivable). Retries like the old reachability probe.
        """
        url = f'http://{host}:{port}/json/version'
        for i in range(self._CDP_ATTEMPTS):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        data = await resp.json()
                        if data.get('webSocketDebuggerUrl'):
                            return data
            except Exception:
                pass
            if i < self._CDP_ATTEMPTS - 1:
                await asyncio.sleep(self._CDP_DELAY)
        return None

    async def _cdp_reachable(self, host: str, port: int) -> bool:
        """Back-compat bool view of :meth:`_cdp_version` (tests)."""
        return await self._cdp_version(host, port) is not None
