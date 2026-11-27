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


def _browser_guid(version: dict) -> str | None:
    """The kernel's browser GUID from a ``/json/version`` payload.

    The tail of ``webSocketDebuggerUrl`` (``.../devtools/browser/
    <uuid>``) is the per-kernel identity CDP exposes; two kernels of
    the same profile on different ports (or the same port at different
    times) never share it.
    """
    ws = str(version.get('webSocketDebuggerUrl') or '')
    marker = '/devtools/browser/'
    return ws.rsplit(marker, 1)[-1] or None if marker in ws else None


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
        self._reopens_hand_opened: bool = False
        # The browser GUID (/json/version's webSocketDebuggerUrl tail)
        # of the kernel THIS backend opened — the identity a later stop
        # matches before tearing anything down (C4: a stale stop may
        # only close what this server opened, never a kernel the user
        # opened by hand on the same profile).
        self._browser_guid: str | None = None

    async def start(self, browser_config: dict) -> BrowserSessionInfo:
        store_ref = browser_config.get('browser_ref', '')
        proxy_port = int(browser_config.get('proxy_port', 9222))
        slug = browser_config.get('store_slug', 'default')

        client = self._client_factory(browser_config)
        self._client = client
        # The vendor's C4 declaration: its stop path may close-and-reopen
        # a hand-opened kernel (injected from the spec by the manager).
        self._reopens_hand_opened = bool(
            browser_config.get('reopens_hand_opened')
        )

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

        version = await self._cdp_version(opened.cdp_host, opened.cdp_port)
        if version is None:
            raise CdpUnreachable(
                f'opened {store_ref} but CDP {opened.cdp_host}:'
                f'{opened.cdp_port} never answered'
            )
        # The identity of the kernel we just opened, for stop().
        self._browser_guid = _browser_guid(version)
        browser = str(version.get('Browser') or '')
        if not browser.startswith(CHROMIUM_BROWSER_PREFIXES):
            # A non-Chromium kernel CAN expose a partial CDP (Firefox's
            # old Remote Agent) — reachable is not drivable. Close the
            # env so nothing half-open lingers, and refuse before
            # after_open or the proxy ever front it.
            logger.warning(
                'store %s: CDP endpoint reports Browser %r (not '
                'Chromium-family); closing the env and refusing',
                store_ref,
                browser,
            )
            await self._close_browser(opened.cdp_host, opened.cdp_port)
            self._opened = None
            try:
                await client.close_store(store_ref)
            except Exception as e:  # never mask the kernel refusal
                logger.warning(
                    'inject backend close_store after kernel refusal '
                    'failed: %s',
                    e,
                )
            finally:
                self._store_ref = None
            raise UnsupportedKernel(
                f"this store's browser kernel reports {browser!r} via "
                'CDP; only Chromium-family kernels are supported '
                f'(expected one of {CHROMIUM_BROWSER_PREFIXES})'
            )

        # A client-specific entry step (an intermediate store-info
        # page) must run
        # BEFORE the proxy: the proxy's startup cleanup closes orphan web
        # tabs, so the store page this produces has to exist first (the
        # engine's keep_last_page keeps it).
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
        """Tear down ONLY what this backend opened (C4).

        The kernel this backend opened is identified by the endpoint it
        recorded at open plus the kernel's browser GUID. A stale stop
        (the session row survived the vendor closing the kernel — the
        user closed the store window, or the client restarted, and then
        opened the profile BY HAND) must never close the hand-opened
        kernel: ``close_store(ref)`` closes whatever kernel the profile
        has NOW, and the fresh start would reopen the profile, losing
        the user's window and tabs. So:

        - endpoint alive + GUID matches (or was never recorded) → the
          kernel is ours: CDP ``Browser.close`` first, then the
          vendor's ``close_store`` (the healthy path, unchanged);
        - endpoint alive + GUID DIFFERS → the port was reused by
          another kernel (another instance): close neither;
        - endpoint dead → the kernel we opened is gone. A
          ``close_store(ref)`` here can only close a kernel that is not
          ours — a hand-opened one. Skip it, unless the vendor's spec
          declares ``reopens_hand_opened`` (its own docs describe
          close-and-reopen, and only its close clears a stale vendor
          state).

        The proxy is always stopped: it is this backend's own.
        """
        owns_kernel = await self._kernel_is_ours()
        reopens = self._client is not None and self._reopens_hand_opened
        if self._opened is not None and owns_kernel:
            # Close the store env itself (CDP Browser.close) BEFORE
            # tearing down the proxy, so the kernel exits cleanly and
            # the next task re-opens a fresh env. Without this,
            # close_store is a no-op and a second task on the same
            # store finds it already open (no new spawn →
            # CdpUnreachable).
            await self._close_browser(
                self._opened.cdp_host, self._opened.cdp_port
            )
        elif self._opened is not None:
            logger.info(
                'stale stop: the kernel this backend opened is gone or '
                'no longer ours (endpoint %s:%s); leaving the profile '
                'kernel alone',
                self._opened.cdp_host,
                self._opened.cdp_port,
            )
        self._opened = None
        try:
            if self._proxy:
                await self._proxy.stop()
                self._proxy = None
        except Exception as e:
            logger.warning('inject backend proxy stop failed: %s', e)
        if self._client is not None and self._store_ref is not None:
            if owns_kernel or reopens:
                try:
                    await self._client.close_store(self._store_ref)
                except Exception as e:
                    logger.warning('inject backend close_store failed: %s', e)
                finally:
                    self._store_ref = None
            else:
                logger.info(
                    'stale stop: skipping close_store(%r) — the kernel '
                    'this backend opened is gone and the profile may '
                    'hold a kernel the user opened by hand '
                    '(vendor does not declare reopens_hand_opened)',
                    self._store_ref,
                )
                self._store_ref = None

    async def _kernel_is_ours(self) -> bool:
        """Whether the kernel this backend opened is still the one on
        the recorded endpoint.

        Probes the recorded endpoint's ``/json/version``: dead → not
        ours (gone); alive with the same browser GUID we recorded at
        open → ours; alive with a DIFFERENT GUID → the port was reused
        by another kernel — not ours. Without a recorded GUID (older
        sessions, or a client that reports none) a live endpoint counts
        as ours — the pre-C4 behaviour for the healthy path.
        """
        if self._opened is None:
            return False
        version = await self._cdp_version(
            self._opened.cdp_host, self._opened.cdp_port
        )
        if version is None:
            return False
        guid = _browser_guid(version)
        if self._browser_guid is None or guid is None:
            return True
        if guid != self._browser_guid:
            logger.warning(
                'endpoint %s:%s now serves browser %r (opened %r) — '
                'the port was reused; not closing it',
                self._opened.cdp_host,
                self._opened.cdp_port,
                guid,
                self._browser_guid,
            )
            return False
        return True

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
