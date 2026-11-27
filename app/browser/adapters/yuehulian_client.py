"""Real client driver for Yuehulian (composes the IO + DOM layers).

Implements :class:`InjectClient` on top of the platform IO layer
(native Windows and WSL both use :mod:`yuehulian_win`; macOS uses
:mod:`yuehulian_mac` — picked in :func:`process_layer` /
:func:`inspector_session`) and
:mod:`yuehulian_dom` (pure parsing, shared by both platforms: the Mac
client is the same Vue/Element-UI app). The read path (ensure_running /
is_logged_in / list_stores) is validated live on both platforms; the
open_store write path (spawn-hook → click → DevToolsActivePort) is
exercised by the live smoke. The inspector is opened per operation and
closed right after, so no code-exec surface lingers.
"""

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import time
import weakref

from app.browser.adapters import yuehulian_mac
from app.browser.adapters.yuehulian_dom import (
    CLEAR_SELECTION_JS,
    CLICK_CLOSE_BROWSER_JS,
    CLICK_NEXT_PAGE_JS,
    CONFIRM_DIALOG_JS,
    LOGIN_HASH_JS,
    NAV_TO_STORE_LIST_JS,
    OPEN_BATCH_MENU_JS,
    SCRAPE_STORES_JS,
    click_open_js,
    fill_team_login_js,
    is_logged_in_hash,
    parse_store_list_json,
    row_label_js,
    select_only_row_js,
    selected_only_js,
)
from app.browser.adapters.yuehulian_win import (
    InspectorSession,
    ProcessLayer,
)
from app.browser.inject.client import InjectClient, OpenedStore, StoreRef
from app.browser.inject.errors import (
    CdpUnreachable,
    ClientNotRunning,
    LoginRequired,
    StoreNotFound,
)
from app.browser.ziniao_utils import get_platform

logger = logging.getLogger(__name__)

_LOCALHOST = '127.0.0.1'

_UI_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _ui_lock() -> asyncio.Lock:
    """The lock serialising every drive of the client GUI (per loop)."""
    loop = asyncio.get_running_loop()
    lock = _UI_LOCKS.get(loop)
    if lock is None:
        lock = _UI_LOCKS[loop] = asyncio.Lock()
    return lock


def process_layer(config: dict) -> ProcessLayer:
    """The platform's process layer (macOS: the Mac twin, else Windows).

    One seam for tests of either platform: the Windows branch goes
    through the module-level ``ProcessLayer`` and the Mac branch
    through ``yuehulian_mac.MacProcessLayer``, so each can be patched.
    """
    if get_platform() == 'mac':
        return yuehulian_mac.MacProcessLayer(config.get('client_path') or None)
    return ProcessLayer(config.get('client_path') or None)


def inspector_session(proc) -> InspectorSession:
    """The platform's inspector session over that process layer."""
    if get_platform() == 'mac':
        return yuehulian_mac.MacInspectorSession(proc)
    return InspectorSession(proc)


class RealYuehulianClient(InjectClient):
    """Drives the real 悦互联 Electron client (Windows, macOS, WSL)."""

    def __init__(self, browser_config: dict | None = None):
        cfg = browser_config or {}
        self._proc = process_layer(cfg)
        # Team-login credentials for auto-login, injected by the manager /
        # the enumerate router from app settings. Empty → no auto-login.
        # The generic injection writes ``<vendor>_<field>`` keys; the
        # legacy short spellings are kept as a fallback for the compat
        # alias route (tests pin them).
        self._team = (
            cfg.get('yuehulian_team_name') or cfg.get('yuehulian_team') or ''
        )
        self._account = (
            cfg.get('yuehulian_subaccount')
            or cfg.get('yuehulian_account')
            or ''
        )
        self._password = cfg.get('yuehulian_password') or ''

    async def ensure_running(self) -> None:
        if await asyncio.to_thread(self._proc.is_running):
            return
        if not await asyncio.to_thread(self._proc.launch):
            detail = self._proc.locate_error or 'launch failed'
            raise ClientNotRunning(
                f'could not locate/launch the client: {detail}'
            )
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
        # One driver of the client's GUI at a time: store setup's list
        # (switch view, page through) and a task's open/close (select,
        # click) act on the same window, and interleaved they misread
        # pages or click a row that has just moved.
        async with _ui_lock():
            pid = await asyncio.to_thread(self._proc.main_pid)
            if not pid:
                raise ClientNotRunning('no main process to inspect')
            insp = inspector_session(self._proc)
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
            # The login tab's inputs render asynchronously AFTER the tab
            # click inside fill_team_login_js, so a first NOFIELDS can
            # mean "form still mounting", not "UI changed" (seen live on
            # the Mac client; a latent race on Windows too, where the
            # team tab simply happened to be active). Poll the same JS
            # until it stops naming missing fields, then give up.
            result = 'NOFIELDS'
            for _ in range(10):
                result = str(
                    await insp.eval_in_renderer(
                        fill_team_login_js(
                            self._team, self._account, self._password
                        )
                    )
                )
                if result != 'NOFIELDS':
                    break
                await asyncio.sleep(0.5)
            if result != 'OK':
                raise LoginRequired(
                    f'team-login form could not be driven: {result}',
                    credentials_stored=True,
                )
            for _ in range(30):
                await asyncio.sleep(1.0)
                route = await insp.eval_in_renderer(LOGIN_HASH_JS)
                if is_logged_in_hash(route):
                    return
            raise LoginRequired(
                'team-login submitted but the client stayed logged out',
                credentials_stored=True,
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
        # KNOWN ISSUE (R8 case C, user decision 2026-11-27): the store is
        # matched by NAME in whatever team the client currently shows.
        # The user configures this client's login themselves, so if they
        # hand-sign it into another team, a same-named store there opens
        # just as well — the user knows this risk. May be fixed later
        # (match by a team-scoped identity instead of the name).
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
            udd, click_ts = await self._open_with_hook(insp, ref)

        try:
            port = await self._await_devtools_port(udd, newer_than=click_ts - 5)
        except CdpUnreachable:
            # A kernel left over from a client that was SIGKILLed can
            # still hold this env's profile (singleton lock): the fresh
            # kernel spawns but never writes DevToolsActivePort. The
            # process layer terminates leftover kernels of THIS env's
            # dir only (a no-op returning [] on Windows, so nothing
            # changes there); when something was actually killed, open
            # the env once more — bounded to one retry.
            killed = await asyncio.to_thread(self._proc.terminate_kernels, udd)
            if not killed:
                raise
            logger.info(
                '悦互联 env %r was held by leftover kernel pid(s) %s; '
                'terminated them and reopening',
                ref,
                killed,
            )
            async with self._inspector() as insp:
                await self._ensure_store_list(insp)
                udd, click_ts = await self._open_with_hook(insp, ref)
            port = await self._await_devtools_port(udd, newer_than=click_ts - 5)
        return OpenedStore(_LOCALHOST, port, udd)

    async def _open_with_hook(
        self, insp: InspectorSession, ref: str
    ) -> tuple[str, float]:
        """Click Open on ``ref`` under the spawn hook; ``(udd, ts)``.

        Handles the stale-Opened state first (see the ALREADYOPEN
        branch). The hook is uninstalled again even on failure.
        """
        await insp.install_spawn_hook()
        try:
            before = len(await insp.recorded_spawns())
            click_ts = time.time()
            result = str(await insp.eval_in_renderer(click_open_js(ref)))
            if result.startswith('ALREADYOPEN'):
                # The client lists the env as open, but not one we run
                # (ours would have answered on its CDP port and never
                # got here): a leftover of a client that was closed or
                # crashed with the env open — the client keeps calling
                # it 'Opened' after a restart — or one a person opened
                # by hand. Neither has a debug port. Close it through
                # the client's own action and open it again, so a task
                # recovers by itself instead of waiting for a human.
                logger.info(
                    '悦互联 lists store %r as already open; closing it '
                    'in the client and reopening with a debug port',
                    ref,
                )
                await self._close_env(insp, ref)
                before = len(await insp.recorded_spawns())
                click_ts = time.time()
                result = str(await insp.eval_in_renderer(click_open_js(ref)))
                if result.startswith('ALREADYOPEN'):
                    raise CdpUnreachable(
                        f'store {ref!r} still reads as open in the 悦互联 '
                        'client after closing it; close it there and '
                        'retry'
                    )
            if not result.startswith('OK'):
                raise StoreNotFound(ref)
            return await self._await_spawn_udd(insp, before), click_ts
        finally:
            await insp.uninstall_spawn_hook()

    async def _close_env(self, insp: InspectorSession, ref: str) -> None:
        """Close ``ref``'s env with the client's own "close browser" batch
        action: tick ONLY its row, More Batch Operations → close browser →
        Confirm, then wait for the row to read 'Open'. Raises
        ``CdpUnreachable`` (the old guidance) when any step is missing, so a
        client UI change fails loudly instead of acting on the wrong row.
        """
        try:
            picked = str(await insp.eval_in_renderer(select_only_row_js(ref)))
            if picked.startswith('CLICKED'):
                # The boxes re-render asynchronously: read the selection
                # back until it settles on exactly this row.
                for _ in range(10):
                    await asyncio.sleep(0.3)
                    picked = str(
                        await insp.eval_in_renderer(selected_only_js(ref))
                    )
                    if picked.startswith('OK'):
                        break
            if not picked.startswith('OK'):
                raise CdpUnreachable(
                    f'could not select store {ref!r} to close it ({picked}); '
                    'close it in the 悦互联 client and retry'
                )
            if await insp.eval_in_renderer(OPEN_BATCH_MENU_JS) != 'OK':
                raise CdpUnreachable(
                    'the 悦互联 client has no "More Batch Operations" menu; '
                    f'close store {ref!r} there and retry'
                )
            clicked = 'NOITEM'
            for _ in range(6):  # the dropdown renders on hover, async
                await asyncio.sleep(0.5)
                clicked = str(
                    await insp.eval_in_renderer(CLICK_CLOSE_BROWSER_JS)
                )
                if clicked == 'CLICKED':
                    break
            if clicked != 'CLICKED':
                raise CdpUnreachable(
                    f'no "close browser" action in the 悦互联 client '
                    f'({clicked[:120]}); close store {ref!r} there and retry'
                )
            for _ in range(10):  # the confirm dialog appears async
                await asyncio.sleep(0.5)
                if await insp.eval_in_renderer(CONFIRM_DIALOG_JS) == (
                    'CONFIRMED'
                ):
                    break
            for _ in range(30):
                await asyncio.sleep(1.0)
                if await insp.eval_in_renderer(row_label_js(ref)) == 'Open':
                    logger.info('Closed leftover 悦互联 env %r', ref)
                    return
            raise CdpUnreachable(
                f'store {ref!r} still reads as open after closing it in the '
                '悦互联 client; close it there and retry'
            )
        finally:
            try:
                await insp.eval_in_renderer(CLEAR_SELECTION_JS)
            except Exception as e:  # best-effort tidy-up
                logger.debug('could not clear the selection: %s', e)

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
