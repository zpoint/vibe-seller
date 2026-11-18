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
    LOGIN_HASH_JS,
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
        if js == SCRAPE_STORES_JS:
            return self.rows
        # otherwise it's the click JS
        if self.click.startswith('OK') and self.udd is not None:
            self._spawns.append({'udd': self.udd, 'ts': 1})
        return self.click

    async def install_spawn_hook(self):
        self.installed = True

    async def uninstall_spawn_hook(self):
        self.uninstalled = True

    async def recorded_spawns(self):
        return list(self._spawns)


def _patched(proc, insp):
    return (
        mock.patch.object(mod, 'ProcessLayer', return_value=proc),
        mock.patch.object(mod, 'InspectorSession', return_value=insp),
        mock.patch.object(mod.asyncio, 'sleep', new=mock.AsyncMock()),
    )


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
