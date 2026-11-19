"""Real Windows-driven Yuehulian client (composes the IO + DOM layers).

Implements :class:`InjectClient` on top of :mod:`yuehulian_win`
(process + inspector) and :mod:`yuehulian_dom` (pure parsing). The read
path (ensure_running / is_logged_in / list_stores) is validated live; the
open_store write path (spawn-hook → click → DevToolsActivePort) is
exercised by the Step 4 e2e. The inspector is opened per operation and
closed right after, so no code-exec surface lingers.
"""

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import time

from app.browser.adapters.yuehulian_dom import (
    CLICK_NEXT_PAGE_JS,
    LOGIN_HASH_JS,
    NAV_TO_STORE_LIST_JS,
    SCRAPE_STORES_JS,
    click_open_js,
    fill_team_login_js,
    is_logged_in_hash,
    parse_store_list_json,
)
from app.browser.adapters.yuehulian_win import InspectorSession, ProcessLayer
from app.browser.inject.client import InjectClient, OpenedStore, StoreRef
from app.browser.inject.errors import (
    CdpUnreachable,
    ClientNotRunning,
    LoginRequired,
    StoreNotFound,
)

logger = logging.getLogger(__name__)

_LOCALHOST = '127.0.0.1'


class RealYuehulianClient(InjectClient):
    """Drives the real 悦互联 Electron client on Windows."""

    def __init__(self, browser_config: dict | None = None):
        cfg = browser_config or {}
        self._proc = ProcessLayer(cfg.get('client_path') or None)
        # Team-login credentials for auto-login (injected by the manager /
        # the enumerate router from app settings). Empty → no auto-login.
        self._team = cfg.get('yuehulian_team') or ''
        self._account = cfg.get('yuehulian_account') or ''
        self._password = cfg.get('yuehulian_password') or ''

    async def ensure_running(self) -> None:
        if await asyncio.to_thread(self._proc.is_running):
            return
        if not await asyncio.to_thread(self._proc.launch):
            raise ClientNotRunning('could not locate/launch the client')
        for _ in range(30):
            await asyncio.sleep(1.0)
            if await asyncio.to_thread(self._proc.main_pid):
                # The MAIN process existing is NOT readiness: a freshly
                # launched client takes several more seconds to create its
                # BrowserWindow and load the app. Returning here let the
                # caller open an inspector against a window-less renderer
                # (getAllWindows()[0] is undefined), which failed open_store
                # on a cold start — it only worked when the client was
                # already up. Block until the renderer can actually run JS.
                await self._await_ready()
                return
        raise ClientNotRunning('client did not come up after launch')

    async def _await_ready(self, *, timeout: float = 90.0) -> None:
        """Wait until the client's renderer can run JS (a BrowserWindow
        exists and executeJavaScript resolves).

        Polls a trivial renderer probe, retrying the inspector open too —
        the main process may not be debuggable the instant it appears. The
        caller can then drive login / store-list / open_store against a
        ready client instead of racing a half-loaded one.
        """
        deadline = time.monotonic() + timeout
        last = ''
        while time.monotonic() < deadline:
            try:
                async with self._inspector() as insp:
                    while time.monotonic() < deadline:
                        try:
                            await insp.eval_in_renderer(
                                LOGIN_HASH_JS, timeout=8.0
                            )
                            return
                        except Exception as e:  # renderer window not up yet
                            last = str(e)
                            await asyncio.sleep(1.0)
            except Exception as e:  # inspector not openable yet
                last = str(e)
                await asyncio.sleep(1.0)
        raise ClientNotRunning(
            f'client launched but its renderer never became ready: {last[:200]}'
        )

    @asynccontextmanager
    async def _inspector(self):
        pid = await asyncio.to_thread(self._proc.main_pid)
        if not pid:
            raise ClientNotRunning('no main process to inspect')
        insp = InspectorSession(self._proc)
        await insp.open(pid)
        try:
            yield insp
        finally:
            await insp.close()

    async def is_logged_in(self) -> bool:
        async with self._inspector() as insp:
            route = await insp.eval_in_renderer(LOGIN_HASH_JS)
        return is_logged_in_hash(route)

    async def ensure_logged_in(self) -> None:
        """Auto-login via 团队登录 when the client is logged out.

        No-op if already logged in. With stored team credentials (from the
        config the manager/router injects), fill and submit the team-login
        form so an expired session doesn't strand tasks (the user: "不要再让
        用户登录了，我们自己登录"). Raises LoginRequired if there are no
        credentials or the login does not take — so the caller surfaces a
        clear "log in / fix credentials" signal rather than flailing. The
        password is only ever in the one eval, never logged.
        """
        async with self._inspector() as insp:
            route = await insp.eval_in_renderer(LOGIN_HASH_JS)
            if is_logged_in_hash(route):
                return
            if not (self._team and self._account and self._password):
                raise LoginRequired()
            result = str(
                await insp.eval_in_renderer(
                    fill_team_login_js(
                        self._team, self._account, self._password
                    )
                )
            )
            if result != 'OK':
                raise LoginRequired(
                    f'team-login form could not be driven: {result}'
                )
            for _ in range(30):
                await asyncio.sleep(1.0)
                route = await insp.eval_in_renderer(LOGIN_HASH_JS)
                if is_logged_in_hash(route):
                    return
            raise LoginRequired(
                'team-login submitted but the client stayed logged out'
            )

    async def list_stores(self) -> list[StoreRef]:
        async with self._inspector() as insp:
            await self._ensure_store_list(insp)
            stores: list[StoreRef] = []
            seen: set[str] = set()
            async for page in self._each_page(insp):
                added = 0
                for s in page:
                    if s.name not in seen:
                        seen.add(s.name)
                        stores.append(s)
                        added += 1
                if stores and added == 0:
                    break  # a repeated page → stuck pager, stop
        return stores

    async def _ensure_store_list(self, insp: InspectorSession) -> None:
        """Confirm login and switch the client to the store-list VIEW.

        A logged-in client can sit on a NON-list view (#/personal-center, a
        store env, ...) where the el-table is not mounted — so a scrape
        there finds zero rows and a store wrongly reads as absent (empty
        enumerate / StoreNotFound), even right after the user logs in. The
        hash can't tell us (it stays '#/index' under the tags-view shell),
        so always click the store-list menu item.
        """
        route = await insp.eval_in_renderer(LOGIN_HASH_JS)
        if not is_logged_in_hash(route):
            raise LoginRequired()
        await insp.eval_in_renderer(NAV_TO_STORE_LIST_JS)

    async def _scrape_page_rows(
        self, insp: InspectorSession, *, attempts: int = 15, delay: float = 1.0
    ) -> list:
        """Poll the CURRENT page's el-table until its rows render (they load
        async). Re-confirms login each round; returns raw rows ([] if the
        page is genuinely empty after the budget)."""
        rows: list = []
        for i in range(attempts):
            route = await insp.eval_in_renderer(LOGIN_HASH_JS)
            if not is_logged_in_hash(route):
                raise LoginRequired()
            rows = await insp.eval_in_renderer(SCRAPE_STORES_JS)
            if parse_store_list_json(json.dumps(rows)):
                return rows
            if i < attempts - 1:
                await asyncio.sleep(delay)
        return rows

    async def _each_page(self, insp: InspectorSession, *, max_pages: int = 50):
        """Yield parsed stores for each pagination page, advancing via the
        Next button. The el-table shows one page at a time, so this is how
        enumerate sees every store and open finds a store on page 2/3.
        Assumes the caller already switched to the store-list view."""
        for _ in range(max_pages):
            rows = await self._scrape_page_rows(insp)
            yield parse_store_list_json(json.dumps(rows))
            nxt = await insp.eval_in_renderer(CLICK_NEXT_PAGE_JS)
            if nxt != 'OK':
                return
            await asyncio.sleep(1.5)  # let the next page re-render

    async def open_store(self, ref: str) -> OpenedStore:
        async with self._inspector() as insp:
            await self._ensure_store_list(insp)
            # Page through until the store's row is on the current page (it
            # may live on page 2/3), then click Open there.
            found = False
            async for page in self._each_page(insp):
                if ref in {s.name for s in page}:
                    found = True
                    break
            if not found:
                raise StoreNotFound(ref)

            await insp.install_spawn_hook()
            try:
                before = len(await insp.recorded_spawns())
                click_ts = time.time()
                result = str(await insp.eval_in_renderer(click_open_js(ref)))
                if result.startswith('ALREADYOPEN'):
                    # The store env is already running — the client won't
                    # spawn a fresh kernel, and the existing one was not
                    # launched with a debug port, so CDP can't attach. A
                    # clean task start/stop closes it (engine Browser.close);
                    # a leftover from an unclean shutdown must be closed in
                    # the client. Report that, not a misleading not-found.
                    raise CdpUnreachable(
                        f'store {ref!r} is already open in the 悦互联 client '
                        'without a debug port; close it there and retry'
                    )
                if not result.startswith('OK'):
                    raise StoreNotFound(ref)
                udd = await self._await_spawn_udd(insp, before)
            finally:
                await insp.uninstall_spawn_hook()

        port = await self._await_devtools_port(udd, newer_than=click_ts - 5)
        return OpenedStore(_LOCALHOST, port, udd)

    async def close_store(self, ref: str) -> None:
        # Best-effort: the env stays open until the client/user closes it
        # (the engine already tore down the CDPMuxProxy). A clean
        # CDP Browser.close / GUI close is a follow-up.
        return None

    async def _await_spawn_udd(
        self, insp: InspectorSession, before: int
    ) -> str:
        for _ in range(20):
            spawns = await insp.recorded_spawns()
            if len(spawns) > before:
                udd = str(spawns[-1].get('udd') or '').strip()
                if udd:
                    return udd
            await asyncio.sleep(0.5)
        raise CdpUnreachable('no kernel spawn was intercepted')

    async def _await_devtools_port(
        self, user_data_dir: str, *, newer_than: float
    ) -> int:
        for _ in range(24):
            port = await asyncio.to_thread(
                self._proc.read_devtools_port, user_data_dir, newer_than
            )
            if port:
                return port
            await asyncio.sleep(0.5)
        raise CdpUnreachable(f'no fresh DevToolsActivePort in {user_data_dir}')
