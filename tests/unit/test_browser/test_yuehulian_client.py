"""RealYuehulianClient orchestration (open_store write path) with fakes.

The real IO (powershell / inspector ws) is validated live + in e2e; here
we pin the open_store branching and error classification with scenario
fakes for the process + inspector layers. asyncio.sleep is stubbed so the
poll loops don't actually wait.
"""

from unittest import mock

import pytest

import app.browser.adapters.yuehulian_client as mod
from app.browser.adapters.yuehulian_client import RealYuehulianClient
from app.browser.adapters.yuehulian_dom import (
    CLICK_NEXT_PAGE_JS,
    LOGIN_HASH_JS,
    NAV_TO_STORE_LIST_JS,
    SCRAPE_STORES_JS,
)
from app.browser.inject.errors import (
    CdpUnreachable,
    ClientNotRunning,
    LoginRequired,
    StoreNotFound,
)

pytestmark = pytest.mark.unit

# One data row (numeric-seq first) that parses to store 'store-a'.
_ROWS = ['1\n\t\nstore-a\n\t\nMarketplaceX\n\t\nRegionA', 'Open']


class _FakeProc:
    def __init__(self, *, running=True, pid=123, port=9222, launch_ok=True):
        self._running = running
        self._pid = pid
        self._port = port
        self._launch_ok = launch_ok

    def is_running(self):
        return self._running

    def main_pid(self):
        return self._pid if self._running else None

    def launch(self):
        self._running = self._launch_ok
        return self._launch_ok

    def read_devtools_port(self, udd, newer_than=None):
        return self._port


class _FakeInspector:
    def __init__(self, *, logged_in=True, rows=None, click='OK:0', udd='C:\\u'):
        self.logged_in = logged_in
        self.rows = rows if rows is not None else _ROWS
        self.click = click
        self.udd = udd
        self._spawns = []
        self.installed = False
        self.uninstalled = False
        self.closed = False

    async def open(self, pid):
        pass

    async def close(self):
        self.closed = True

    async def eval_in_renderer(self, js, **kw):
        if js == LOGIN_HASH_JS:
            return '#/index' if self.logged_in else '#/login'
        if js == NAV_TO_STORE_LIST_JS:
            return 'OK'
        if js == CLICK_NEXT_PAGE_JS:
            return 'END'  # single page by default
        if js == SCRAPE_STORES_JS:
            return self.rows
        # otherwise it's the click-open JS
        if self.click.startswith('OK') and self.udd is not None:
            self._spawns.append({'udd': self.udd, 'ts': 1})
        return self.click

    async def install_spawn_hook(self):
        self.installed = True

    async def uninstall_spawn_hook(self):
        self.uninstalled = True

    async def recorded_spawns(self):
        return list(self._spawns)


class _SlowTableInspector(_FakeInspector):
    """Returns an empty store table for the first ``empty_rounds`` scrapes,
    then the populated rows — simulating the el-table rendering late on a
    freshly started / just-logged-in client."""

    def __init__(self, *, empty_rounds, **kw):
        super().__init__(**kw)
        self._empty_rounds = empty_rounds
        self.scrape_calls = 0

    async def eval_in_renderer(self, js, **kw):
        if js == SCRAPE_STORES_JS:
            self.scrape_calls += 1
            if self.scrape_calls <= self._empty_rounds:
                return []  # table not rendered yet
            return self.rows
        return await super().eval_in_renderer(js, **kw)


class _PaginatedInspector(_FakeInspector):
    """Serves one page of store rows at a time; CLICK_NEXT advances to the
    next page until the last (then 'END'), NAV resets to page 1 — models the
    el-table pagination so enumerate/open must page through."""

    def __init__(self, *, pages, **kw):
        super().__init__(**kw)
        self._pages = pages
        self._page = 0

    async def eval_in_renderer(self, js, **kw):
        if js == NAV_TO_STORE_LIST_JS:
            self._page = 0
            return 'OK'
        if js == CLICK_NEXT_PAGE_JS:
            if self._page < len(self._pages) - 1:
                self._page += 1
                return 'OK'
            return 'END'
        if js == SCRAPE_STORES_JS:
            return self._pages[self._page]
        return await super().eval_in_renderer(js, **kw)


class _LoginInspector(_FakeInspector):
    """Starts logged OUT; after the team-login form is submitted it reports
    logged-in iff ``logs_in`` — models auto-login + its failure modes."""

    def __init__(self, *, logs_in=True, form_result='OK', **kw):
        super().__init__(logged_in=False, **kw)
        self._logs_in = logs_in
        self._form_result = form_result
        self.submitted = False

    async def eval_in_renderer(self, js, **kw):
        if js == LOGIN_HASH_JS:
            return '#/index' if (self.submitted and self._logs_in) else ''
        if '团队登录' in js:  # the fill_team_login_js payload
            self.submitted = True
            return self._form_result
        return await super().eval_in_renderer(js, **kw)


class _NotReadyInspector(_FakeInspector):
    """Renderer has no BrowserWindow for the first ``not_ready_rounds``
    probes (a freshly launched client still creating its window), then it
    responds — models the cold-start gap between the MAIN process appearing
    and the renderer being driveable."""

    def __init__(self, *, not_ready_rounds, **kw):
        super().__init__(**kw)
        self._not_ready = not_ready_rounds
        self.probe_calls = 0

    async def eval_in_renderer(self, js, **kw):
        if js == LOGIN_HASH_JS:
            self.probe_calls += 1
            if self.probe_calls <= self._not_ready:
                raise RuntimeError('getAllWindows()[0] is undefined')
        return await super().eval_in_renderer(js, **kw)


def _patched(proc, insp):
    return (
        mock.patch.object(mod, 'ProcessLayer', return_value=proc),
        mock.patch.object(mod, 'InspectorSession', return_value=insp),
        mock.patch.object(mod.asyncio, 'sleep', new=mock.AsyncMock()),
    )


_CREDS = {
    'yuehulian_team': 't',
    'yuehulian_account': 'a',
    'yuehulian_password': 'p',
}


async def _ensure_login(insp, cfg):
    p1, p2, p3 = _patched(_FakeProc(), insp)
    with p1, p2, p3:
        await RealYuehulianClient(cfg).ensure_logged_in()


async def _open(proc, insp, ref='store-a'):
    p1, p2, p3 = _patched(proc, insp)
    with p1, p2, p3:
        client = RealYuehulianClient({})
        return await client.open_store(ref)


async def test_open_store_happy_path():
    insp = _FakeInspector(udd='C:\\udd\\x')
    opened = await _open(_FakeProc(port=9333), insp)
    assert opened.cdp_host == '127.0.0.1'
    assert opened.cdp_port == 9333
    assert opened.user_data_dir == 'C:\\udd\\x'
    assert insp.installed and insp.uninstalled and insp.closed


async def test_open_store_unknown_store_is_store_not_found():
    insp = _FakeInspector(rows=['9\n\t\nother\n\t\nMX'])
    with pytest.raises(StoreNotFound):
        await _open(_FakeProc(), insp, ref='store-a')
    # never touched the spawn hook for a store that does not exist
    assert insp.installed is False


async def test_open_store_not_logged_in_is_login_required():
    insp = _FakeInspector(logged_in=False)
    with pytest.raises(LoginRequired):
        await _open(_FakeProc(), insp)
    assert insp.installed is False


async def test_open_store_no_spawn_is_cdp_unreachable():
    # Click "succeeds" but no kernel spawn is intercepted → unreachable,
    # and the hook is still cleaned up.
    insp = _FakeInspector(udd=None)
    with pytest.raises(CdpUnreachable):
        await _open(_FakeProc(), insp)
    assert insp.installed and insp.uninstalled


async def test_open_store_no_port_is_cdp_unreachable():
    insp = _FakeInspector(udd='C:\\udd\\x')
    with pytest.raises(CdpUnreachable):
        await _open(_FakeProc(port=None), insp)


async def test_open_store_already_open_is_cdp_unreachable_not_not_found():
    # The store exists but its env is already open ('Opened') — the client
    # won't spawn a fresh debug-port kernel, so CDP can't attach. Report it
    # clearly (CdpUnreachable), NOT a misleading StoreNotFound, and still
    # clean up the spawn hook. The happy path above (click 'OK:0' → spawn →
    # success) is the negative control for this branch.
    insp = _FakeInspector(click='ALREADYOPEN:Opened')
    with pytest.raises(CdpUnreachable):
        await _open(_FakeProc(), insp)
    with pytest.raises(StoreNotFound):
        # sanity: the two are distinct outcomes, not the same raise
        insp2 = _FakeInspector(click='NOTFOUND')
        await _open(_FakeProc(), insp2)
    assert insp.installed and insp.uninstalled


async def test_list_stores_waits_for_the_table_to_render():
    # The store table renders async; the first few scrapes see 0 rows. The
    # enumerate must poll until rows appear instead of reporting "no stores"
    # on a freshly started / just-logged-in client (the user-reported bug).
    insp = _SlowTableInspector(empty_rounds=3)
    p1, p2, p3 = _patched(_FakeProc(), insp)
    with p1, p2, p3:
        stores = await RealYuehulianClient({}).list_stores()
    assert [s.name for s in stores] == ['store-a']
    assert insp.scrape_calls >= 4  # polled past the empty rounds


async def test_list_stores_twist_empty_login_returns_empty_not_hang():
    # Negative control: the table never populates (a genuinely empty login)
    # → return [] after the bounded budget, never an unbounded wait.
    insp = _SlowTableInspector(empty_rounds=10_000)
    p1, p2, p3 = _patched(_FakeProc(), insp)
    with p1, p2, p3:
        stores = await RealYuehulianClient({}).list_stores()
    assert stores == []
    assert insp.scrape_calls == 15  # the attempts budget, then gives up


async def test_list_stores_collects_across_pages():
    # Enumerate must page through the el-table (user runs 5/page with >5
    # stores) — not just return page 1. Negative control: pre-pagination it
    # returned only the first page.
    p1 = ['1\n\t\npage1-a\n\t\nMX', '2\n\t\npage1-b\n\t\nMX']
    p2 = ['3\n\t\npage2-c\n\t\nMX']
    insp = _PaginatedInspector(pages=[p1, p2])
    a, b, c = _patched(_FakeProc(), insp)
    with a, b, c:
        stores = await RealYuehulianClient({}).list_stores()
    assert [s.name for s in stores] == ['page1-a', 'page1-b', 'page2-c']


async def test_list_stores_single_page_terminates():
    # Twist: one page (CLICK_NEXT → 'END') → just that page, no hang.
    insp = _FakeInspector(rows=['1\n\t\nonly-one\n\t\nMX'])
    a, b, c = _patched(_FakeProc(), insp)
    with a, b, c:
        stores = await RealYuehulianClient({}).list_stores()
    assert [s.name for s in stores] == ['only-one']


async def test_open_store_finds_store_on_page_two():
    # A store on page 2/3 must still be openable — open pages through until
    # the row is present, then clicks Open there.
    p1 = ['1\n\t\npage1-a\n\t\nMX']
    p2 = ['2\n\t\ntarget\n\t\nMX']
    insp = _PaginatedInspector(pages=[p1, p2], udd='C:\\udd\\z')
    opened = await _open(_FakeProc(port=9555), insp, ref='target')
    assert opened.cdp_port == 9555


async def test_open_store_absent_across_all_pages_is_not_found():
    # Twist: a store on no page → StoreNotFound after exhausting pages.
    p1 = ['1\n\t\npage1-a\n\t\nMX']
    p2 = ['2\n\t\npage2-b\n\t\nMX']
    insp = _PaginatedInspector(pages=[p1, p2])
    with pytest.raises(StoreNotFound):
        await _open(_FakeProc(), insp, ref='ghost')


async def test_ensure_logged_in_noop_when_already_logged_in():
    insp = _FakeInspector()  # logged_in=True by default
    await _ensure_login(insp, _CREDS)  # returns without touching the form


async def test_ensure_logged_in_auto_logs_in_when_logged_out():
    insp = _LoginInspector(logs_in=True)
    await _ensure_login(insp, _CREDS)
    assert insp.submitted  # drove the team-login form


async def test_ensure_logged_in_no_creds_raises_without_touching_form():
    insp = _LoginInspector()
    with pytest.raises(LoginRequired):
        await _ensure_login(insp, {})  # no creds in config
    assert insp.submitted is False  # no creds → never try the form


async def test_ensure_logged_in_form_not_driveable_raises():
    # Twist: the form could not be driven (UI changed) → LoginRequired.
    insp = _LoginInspector(form_result='NOFIELDS')
    with pytest.raises(LoginRequired):
        await _ensure_login(insp, _CREDS)


async def test_ensure_logged_in_submitted_but_stays_logged_out_raises():
    # Twist: form submitted OK but login never took (bad creds) → raise, not
    # a false success.
    insp = _LoginInspector(logs_in=False, form_result='OK')
    with pytest.raises(LoginRequired):
        await _ensure_login(insp, _CREDS)
    assert insp.submitted


async def test_ensure_running_launches_when_down():
    proc = _FakeProc(running=False, launch_ok=True)
    p1, p2, p3 = _patched(proc, _FakeInspector())
    with p1, p2, p3:
        await RealYuehulianClient({}).ensure_running()
    assert proc.is_running() is True


async def test_ensure_running_twist_launch_fails_is_client_not_running():
    proc = _FakeProc(running=False, launch_ok=False)
    p1, p2, p3 = _patched(proc, _FakeInspector())
    with p1, p2, p3, pytest.raises(ClientNotRunning):
        await RealYuehulianClient({}).ensure_running()


async def test_ensure_running_waits_for_renderer_ready_after_launch():
    """Cold start: ensure_running must not return until the renderer can run
    JS. The MAIN process appears at once but the BrowserWindow is created a
    few seconds later; returning early let open_store race a window-less
    renderer — the 'main browser start failed' the user hit on a client they
    had just exited. The probe must retry past the not-ready rounds."""
    proc = _FakeProc(running=False, launch_ok=True)
    insp = _NotReadyInspector(not_ready_rounds=3)
    p1, p2, p3 = _patched(proc, insp)
    with p1, p2, p3:
        await RealYuehulianClient({}).ensure_running()
    # 3 not-ready probes + 1 that succeeds = the renderer was polled, not
    # assumed ready from main_pid alone (pre-fix: 0 renderer probes).
    assert insp.probe_calls >= 4
