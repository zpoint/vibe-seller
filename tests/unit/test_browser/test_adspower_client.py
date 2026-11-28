"""AdsPowerClient against a fake AdsPower API server (the live shapes).

Discovery, auth-error mapping, pagination, start/idempotent restart,
rate-limit resend, kernel-download polling, every D7 empty-start
branch (with a FAKE process lister), stop, param pinning and key
redaction — all on the shapes verified live 2026-11-27 (client
v8.9.23). Placeholder data only.
"""

import logging
import time
from unittest import mock

import pytest

from app.browser.adapters import adspower as ads_client
from app.browser.adapters.adspower import AdsPowerClient
import app.browser.adapters.adspower_backend as ads_backend
from app.browser.adapters.adspower_common import KernelProcess
from app.browser.inject.client import OpenedStore, StoreRef
from app.browser.inject.errors import (
    AccountMismatch,
    ApiNotEnabled,
    CdpUnreachable,
    ClientNotRunning,
    InjectError,
    LoginRequired,
    StoreNotFound,
    StoreUnavailable,
    UnsupportedKernel,
)
from app.browser.local_api_client import LocalApiClient
from tests.unit.test_browser.fake_adspower_api import (
    FakeAdsPowerApi,
    _store,
)

pytestmark = pytest.mark.unit

_KEY = 'test-key-123'


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    # The real 1.2 s gap and waits are live behaviour, not CI.
    monkeypatch.setattr(AdsPowerClient, 'min_interval_s', 0.0)
    monkeypatch.setattr(AdsPowerClient, 'connect_timeout_s', 0.5)
    monkeypatch.setattr(AdsPowerClient, 'request_timeout_s', 0.5)
    monkeypatch.setattr(AdsPowerClient, 'LAUNCH_POLL_S', 0.05)
    monkeypatch.setattr(AdsPowerClient, 'KERNEL_POLL_S', 0.05)
    monkeypatch.setattr(AdsPowerClient, 'KERNEL_TIMEOUT_S', 1.0)
    monkeypatch.setattr(AdsPowerClient, 'GUI_WAIT_S', 0.5)


@pytest.fixture
async def api():
    server = FakeAdsPowerApi()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


class FakeProc:
    """A fake AdsPowerProcessLayer (launch / headless / kernels)."""

    def __init__(self, *, shell_running=True):
        self.shell_running_value = shell_running
        self.locate_error = None
        self.launch_calls = 0

    def shell_running(self) -> bool:
        return self.shell_running_value

    def launch(self) -> bool:
        self.launch_calls += 1
        return True


def _client(api: FakeAdsPowerApi, **proc_kw) -> AdsPowerClient:
    c = AdsPowerClient({'adspower_api_key': _KEY})
    c._port = api.port
    c._read_port = lambda: api.port
    c._proc = FakeProc(**proc_kw)
    return c


# ── discovery (D2): file port, stale file, missing file ──────────────


async def test_discovery_uses_file_port_and_status(api):
    c = _client(api)
    c._port = None  # force discovery through the file seam
    assert await c._api_up() is True
    assert c._port == api.port


async def test_discovery_stale_file_counts_as_not_running(api):
    # The file names a port nothing listens on (a killed client):
    # /status on it never answers → "not running".
    c = AdsPowerClient({'adspower_api_key': _KEY})
    c._read_port = lambda: 59999
    c._port = 59999
    assert await c._api_up() is False


async def test_discovery_missing_file_falls_back_to_default(api):
    c = AdsPowerClient({'adspower_api_key': _KEY})
    c._read_port = lambda: None
    assert await c._api_up() is False
    assert c._port == AdsPowerClient.api_port


# ── auth mapping (D8) ────────────────────────────────────────────────


async def test_wrong_key_maps_to_account_mismatch(api):
    c = AdsPowerClient({'adspower_api_key': 'not-the-key'})
    c._port = api.port
    c._read_port = lambda: api.port
    c._proc = FakeProc()
    with pytest.raises(AccountMismatch):
        await c.list_stores()


async def test_wrong_key_error_text_hides_the_key(api, caplog):
    # R4: the key never appears in exception text or logs.
    caplog.set_level(logging.DEBUG)
    c = AdsPowerClient({'adspower_api_key': 'very-secret-key-xyz'})
    c._port = api.port
    c._read_port = lambda: api.port
    c._proc = FakeProc()
    with pytest.raises(AccountMismatch):
        await c.list_stores()
    assert 'very-secret-key-xyz' not in caplog.text


# ── list paging + parsing ────────────────────────────────────────────


async def test_list_pages_until_short_page(api):
    c = _client(api)
    stores = await c.list_stores()
    # R18: name is the profile's own name, ref the stable open id —
    # the picker's opaque-ref label shows the display first, id last.
    assert [s.name for s in stores] == ['demo-store', 'demo-firefox']
    assert [s.open_ref for s in stores] == ['kaaaaaa1', 'kaaaaaa2']
    assert stores[0].remote_env == '#1 demo-store (acme)'
    assert stores[0].platform == 'www.amazon.com'
    assert stores[0].available is True


async def test_list_paging_negative_control(api):
    # Shrinking the page size must page MORE, not truncate silently.
    c = _client(api)
    c.LIST_PAGE_SIZE = 1
    stores = await c.list_stores()
    assert len(stores) == 2
    list_calls = [p for p, _ in api.calls if p == '/api/v1/user/list']
    # Page 1, 2 (full pages) + page 3 (the empty short page that ends
    # the walk) — the extra call is how "no more pages" is detected.
    assert len(list_calls) == 3


async def test_list_unreachable_maps_to_client_not_running(api):
    c = _client(api)
    c._read_port = lambda: 59999
    c._port = 59999
    with pytest.raises(ClientNotRunning):
        await c.list_stores()


async def test_is_logged_out_keyless_require_api_key():
    # The not-authenticated shape (no key stored) is LOGIN_REQUIRED.
    c = AdsPowerClient({})
    assert c.is_logged_out(200, None) is False
    assert c.is_logged_out(200, {'code': -1, 'msg': 'Require api-key'}) is True


async def test_is_logged_out_keyed_client_never_logs_out():
    # A key is stored: refusals carry real reasons (AccountMismatch),
    # never a logged-out verdict — verified live (FACTS §c).
    c = AdsPowerClient({'adspower_api_key': _KEY})
    assert c.is_logged_out(200, {'code': -1, 'msg': 'Require api-key'}) is False


# ── R16: keyed/keyless "Require api-key" carry different advice ────


async def test_keyed_require_api_key_is_sign_in_needed(api):
    # R16b: the key IS stored and sent, and the API still answers
    # "Require api-key" — it does not recognize the session. That is
    # the sign-in-needed shape: LoginRequired with the SAME wording
    # the store form shows, never the "no API key" text.
    api.always_require = True
    c = _client(api)
    with pytest.raises(LoginRequired) as ei:
        await c.list_stores()
    assert ads_client.SIGN_IN_NEEDED_ZH in str(ei.value)
    assert ads_client.SIGN_IN_NEEDED_EN in str(ei.value)
    assert '还没有配置' not in str(ei.value)


async def test_keyed_require_api_key_start_refuses_before_open(api):
    # The same shape on the START path: map_error's LoginRequired is
    # raised before _decide_failed_start can mistake it for a kernel
    # problem (the raise-early tuple includes it).
    api.always_require = True
    c = _client(api)
    with pytest.raises(LoginRequired) as ei:
        await c.open_store('kaaaaaa1')
    assert ads_client.SIGN_IN_NEEDED_ZH in str(ei.value)


async def test_keyless_require_api_key_keeps_the_advice(api):
    # R16a: no key stored → the not-authenticated shape stays
    # LOGIN_REQUIRED (the shared contract), and the generate-key
    # advice rides the exception message.
    c = AdsPowerClient({})  # keyless
    c._port = api.port
    c._read_port = lambda: api.port
    c._proc = FakeProc()
    with pytest.raises(LoginRequired) as ei:
        await c.list_stores()
    assert ads_client.NO_KEY_ADVICE_ZH in str(ei.value)
    assert ads_client.NO_KEY_ADVICE_EN in str(ei.value)


# ── start: happy path, params, idempotence ───────────────────────────


async def test_start_returns_cdp_endpoint_and_params(api):
    c = _client(api)
    opened = await c.open_store('kaaaaaa1')
    assert isinstance(opened, OpenedStore)
    assert opened.cdp_host == '127.0.0.1'
    assert opened.cdp_port == api.cdp_port
    start_calls = [(p, q) for p, q in api.calls if p == '/api/v1/browser/start']
    assert start_calls, 'start was not called'
    _, params = start_calls[0]
    # m4: no AdsPower start page, open the profile's own platform URL.
    assert params['ip_tab'] == '0'
    assert params['open_tabs'] == '0'
    assert params['user_id'] == 'kaaaaaa1'


async def test_start_is_idempotent_same_endpoint(api):
    first = await _client(api).open_store('kaaaaaa1')
    c = _client(api)
    second = await c.open_store('kaaaaaa1')
    assert second.cdp_port == first.cdp_port


async def test_start_unknown_id_maps_to_store_not_found(api):
    c = _client(api)
    with pytest.raises(StoreNotFound):
        await c.open_store('zzzz9999')


# ── rate limit: resend, bounded ──────────────────────────────────────


async def test_rate_limit_is_resent(api):
    api.rate_limit_burst = 2
    c = _client(api)
    stores = await c.list_stores()
    assert len(stores) == 2


async def test_rate_limit_bounded_negative_control(api, monkeypatch):
    # A rate limit that never clears surfaces as an error, not a loop.
    api.rate_limit_burst = 10_000
    c = _client(api)
    monkeypatch.setattr(c, 'RATE_LIMIT_RETRIES', 2)
    with pytest.raises((ApiNotEnabled, Exception)):
        await c.list_stores()


# ── kernel download: poll, bounded ───────────────────────────────────


async def test_kernel_download_is_polled(api):
    api.start_results['kaaaaaa1'] = 'downloading'
    c = _client(api)

    # The first start says "downloading"; the next answers CDP.
    calls = {'n': 0}

    async def fake_api_call(method, path, json_body=None):
        calls['n'] += 1
        if calls['n'] > 1:
            api.start_results['kaaaaaa1'] = None
        return await LocalApiClient.api_call(c, method, path)

    with mock.patch.object(c, 'api_call', side_effect=fake_api_call):
        opened = await c.open_store('kaaaaaa1')
    assert opened.cdp_port == api.cdp_port


async def test_kernel_not_ready_gets_the_actionable_hint(api):
    # R11: 'is not ready' means the kernel is MISSING and nothing
    # downloads it in the background (verified live on macOS). The
    # brief poll runs, then the error names the kernel and tells the
    # user what to do — NOT 'downloading, retry shortly'.
    api.start_results['kaaaaaa1'] = 'not_ready'
    c = _client(api)
    c.KERNEL_NOT_READY_WAIT_S = 0.05
    with pytest.raises(StoreUnavailable) as ei:
        await c.open_store('kaaaaaa1')
    assert 'SunBrowser 156' in str(ei.value)
    assert '后台不会自动下载' in str(ei.value)
    assert '正在下载' not in str(ei.value)


async def test_kernel_updating_gets_the_downloading_hint(api):
    # R11 counterpart: 'is updating' IS a running download (Windows
    # GUI: ready in ~40 s) — the full budget and the retry hint.
    api.start_results['kaaaaaa1'] = 'downloading'
    c = _client(api)
    c.KERNEL_TIMEOUT_S = 0.05
    with pytest.raises(StoreUnavailable) as ei:
        await c.open_store('kaaaaaa1')
    assert '正在下载浏览器内核' in str(ei.value)


# ── empty start (D7): firefox vs orphan vs neither ──────────────────


def _kernel(pid: int, name: str, ref: str) -> KernelProcess:
    return KernelProcess(
        pid,
        name,
        f'C:\\.ADSPOWER_GLOBAL\\cache\\{ref}_i7test{"_flower" if "flower" in name else ""}',
    )


async def test_empty_start_firefox_maps_to_unsupported_kernel(api):
    api.start_results['kaaaaaa2'] = 'firefox'
    c = _client(api)
    killed: list[int] = []
    c._list_kernels = lambda: [
        _kernel(101, 'flowerbrowser.exe', 'kaaaaaa2'),
        _kernel(202, 'sunbrowser.exe', 'kother999'),
    ]
    with mock.patch.object(c, '_kill_tree', side_effect=killed.append):
        with pytest.raises(UnsupportedKernel) as ei:
            await c.open_store('kaaaaaa2')
    # The hint says what to do; only OUR window's tree was killed.
    assert 'SunBrowser' in str(ei.value)
    assert killed == [101]


async def test_empty_start_orphan_recovers(api):
    # A leftover SunBrowser of a killed client locks the profile: kill
    # that tree only, then start again → CDP.
    calls = {'n': 0}
    c = _client(api)

    async def fake_api_call(method, path, json_body=None):
        calls['n'] += 1
        if path.startswith('/api/v1/browser/start') and calls['n'] == 1:
            api.start_results['kaaaaaa1'] = None
            return 200, {
                'code': 0,
                'msg': 'success',
                'data': {
                    'ws': {'puppeteer': '', 'selenium': ''},
                    'debug_port': '',
                    'webdriver': '',
                },
            }
        return await LocalApiClient.api_call(c, method, path)

    c._list_kernels = lambda: [_kernel(303, 'sunbrowser.exe', 'kaaaaaa1')]
    with mock.patch.object(c, '_kill_tree', return_value=True):
        with mock.patch.object(c, 'api_call', side_effect=fake_api_call):
            opened = await c.open_store('kaaaaaa1')
    assert opened.cdp_port == api.cdp_port


async def test_empty_start_orphan_still_empty_maps_to_cdp(api):
    calls = {'n': 0}
    c = _client(api)

    async def always_empty(method, path, json_body=None):
        calls['n'] += 1
        if path.startswith('/api/v1/browser/start'):
            return 200, {
                'code': 0,
                'msg': 'success',
                'data': {
                    'ws': {'puppeteer': '', 'selenium': ''},
                    'debug_port': '',
                    'webdriver': '',
                },
            }
        return await LocalApiClient.api_call(c, method, path)

    c._list_kernels = lambda: [_kernel(404, 'sunbrowser.exe', 'kaaaaaa1')]
    with mock.patch.object(c, '_kill_tree', return_value=True):
        with mock.patch.object(c, 'api_call', side_effect=always_empty):
            with pytest.raises(CdpUnreachable):
                await c.open_store('kaaaaaa1')


async def test_empty_start_no_process_maps_to_cdp(api):
    api.start_results['kaaaaaa2'] = 'firefox'
    c = _client(api)
    c._list_kernels = lambda: []
    with pytest.raises(CdpUnreachable):
        await c.open_store('kaaaaaa2')


async def test_kernel_attribution_only_matches_exact_id():
    # A longer id that starts with the same characters must not match.
    kernels = [
        _kernel(1, 'sunbrowser.exe', 'kaaaaaa1'),
        _kernel(2, 'sunbrowser.exe', 'kaaaaaa12'),
    ]
    mine = AdsPowerClient._kernels_for(kernels, 'kaaaaaa1')
    assert [k.pid for k in mine] == [1]


# ── stop ─────────────────────────────────────────────────────────────


async def test_stop_not_open_is_benign(api):
    api.start_results['kaaaaaa2'] = 'firefox'
    c = _client(api)
    await c.close_store('kaaaaaa2')  # must not raise


async def test_stop_passes_user_id(api):
    c = _client(api)
    await c.close_store('kaaaaaa1')
    stop_calls = [q for p, q in api.calls if p == '/api/v1/browser/stop']
    assert stop_calls and stop_calls[0]['user_id'] == 'kaaaaaa1'


# ── ensure_running (D4) ─────────────────────────────────────────────


async def test_ensure_running_up_never_launches(api):
    c = _client(api)
    await c.ensure_running()
    assert c._proc.launch_calls == 0


async def test_ensure_running_launches_gui_when_absent(api):
    c = _client(api, shell_running=False)
    c._read_port = lambda: 59999  # nothing up yet
    c._port = 59999
    called = []

    async def fake_wait(budget):
        called.append(budget)
        # Simulate the GUI coming up: the API answers after this.
        c._port = api.port
        return True

    with mock.patch.object(c, '_wait_for_api', side_effect=fake_wait):
        await c.ensure_running()
    assert c._proc.launch_calls == 1


async def test_signed_out_refuses_with_the_sign_in_text(api):
    # R22 (the user's option (a)): a signed-out shell (up, API down)
    # refuses with the R16 sign-in wording — the SAME text the store
    # form's LoginRequired override shows — and spawns NOTHING (no
    # headless instance, no extra process of any kind).
    c = _client(api, shell_running=True)
    c._read_port = lambda: 59999
    c._port = 59999
    with mock.patch.object(
        c._proc, 'launch', side_effect=AssertionError('must not spawn')
    ):
        with pytest.raises(LoginRequired) as ei:
            await c.ensure_running()
    assert ads_client.SIGN_IN_NEEDED_ZH in str(ei.value)
    assert ads_client.SIGN_IN_NEEDED_EN in str(ei.value)
    assert '还没有配置' not in str(ei.value)


async def test_signed_out_without_a_key_refuses_the_same(api):
    # R22: key or no key, the signed-out shape refuses identically —
    # the key no longer feeds any fallback.
    c = AdsPowerClient({})
    c._read_port = lambda: 59999
    c._port = 59999
    c._proc = FakeProc(shell_running=True)
    with pytest.raises(LoginRequired) as ei:
        await c.ensure_running()
    assert ads_client.SIGN_IN_NEEDED_ZH in str(ei.value)


async def test_ensure_running_always_ends_within_its_budgets(api):
    # R16/R22: every wait is budget-bounded, so ensure_running ALWAYS
    # ends. Real _wait_for_api (not mocked): the shell runs but the
    # API never comes up — the refusal arrives immediately (no grace,
    # no fallback waits to run out).
    c = _client(api, shell_running=True)
    c._read_port = lambda: 59999  # nothing ever answers
    c._port = 59999
    t0 = time.monotonic()
    with pytest.raises(LoginRequired):
        await c.ensure_running()
    elapsed = time.monotonic() - t0
    assert elapsed < 3.0, f'ensure_running took {elapsed:.1f}s'


async def test_ensure_running_launch_failure_is_client_not_running(api):
    # The no-shell path: nothing to launch (not installed / not
    # found) → ClientNotRunning, never a hang or a login prompt.
    c = _client(api, shell_running=False)
    c._read_port = lambda: 59999
    c._port = 59999
    c._proc.locate_error = '没找到 AdsPower：请先安装并打开 AdsPower。'
    with mock.patch.object(c._proc, 'launch', return_value=False) as launch:
        with pytest.raises(ClientNotRunning) as ei:
            await c.ensure_running()
    assert launch.call_count == 1  # one attempt, no loop
    assert '没找到' in str(ei.value)


# ── parse_stores / parse_opened details ─────────────────────────────


def test_parse_stores_shapes():
    c = AdsPowerClient({})
    resp = {'data': {'list': [_store('kaaaaaa1', 'demo-store', serial='7')]}}
    (store,) = c.parse_stores(resp)
    assert store == StoreRef(
        name='demo-store',
        platform='www.amazon.com',
        remote_env='#7 demo-store (acme)',
        ref='kaaaaaa1',
        available=True,
    )


def test_picker_row_binds_by_the_profile_id():
    # R18: the row the picker renders and the value a store binds by.
    # The label (vendorStoreOptionLabel, the opaque-ref shape) reads
    # display · platform · ref — pinned end-to-end by the vitest
    # (adspowerPickerLabel.test.ts) on this same recorded row.
    c = AdsPowerClient({})
    resp = {'data': {'list': [_store('kaaaaaa1', '示例甲1店', serial='1')]}}
    (store,) = c.parse_stores(resp)
    assert store.name == '示例甲1店'
    assert store.remote_env == '#1 示例甲1店 (acme)'
    assert store.platform == 'www.amazon.com'
    assert store.ref == 'kaaaaaa1'
    # the BIND value — what the picker binds and the engine reopens
    # by — is the profile id, unchanged.
    assert store.open_ref == 'kaaaaaa1'


def test_parse_opened_from_debug_port_only():
    # ws.selenium missing → fall back to debug_port (the base class
    # contract resolves /json/version on the engine side).
    c = AdsPowerClient({})
    opened = c.parse_opened({
        'data': {'ws': {}, 'debug_port': '57094', 'webdriver': 'x'}
    })
    assert opened is not None
    assert (opened.cdp_host, opened.cdp_port) == ('127.0.0.1', 57094)


def test_parse_opened_empty_is_none():
    c = AdsPowerClient({})
    assert (
        c.parse_opened({
            'data': {
                'ws': {'puppeteer': '', 'selenium': ''},
                'debug_port': '',
                'webdriver': '',
            }
        })
        is None
    )


# ── R1: EVERY failed start goes through the ONE decision function ──


async def test_startup_timeout_with_firefox_window_is_unsupported(api):
    # FACTS §m7: a FlowerBrowser start can answer code -1
    # "Browser execution startup timeout" while the window STILL opens.
    # The decision function must recognize it from process evidence
    # and clean up our window — not hand the user a meaningless
    # timeout error with a browser left behind.
    calls = {'n': 0}

    async def timeout_then_ok(method, path, json_body=None):
        calls['n'] += 1
        if path.startswith('/api/v1/browser/start') and calls['n'] == 1:
            return 200, {
                'code': -1,
                'msg': 'Browser execution startup timeout',
            }
        return await LocalApiClient.api_call(c, method, path)

    c = _client(api)
    api.start_results['kaaaaaa2'] = 'firefox'  # stop is refused (§j)
    c._list_kernels = lambda: [_kernel(501, 'flowerbrowser.exe', 'kaaaaaa2')]
    killed: list[int] = []
    with mock.patch.object(c, '_kill_tree', side_effect=killed.append):
        with mock.patch.object(c, 'api_call', side_effect=timeout_then_ok):
            with pytest.raises(UnsupportedKernel):
                await c.open_store('kaaaaaa2')
    assert killed == [501]


async def test_startup_timeout_no_kernel_is_store_unavailable(api):
    # Neither kernel ran: the vendor's own message surfaces (R1's
    # "existing error for that shape" for a code -1).
    async def always_timeout(method, path, json_body=None):
        if path.startswith('/api/v1/browser/start'):
            return 200, {
                'code': -1,
                'msg': 'Browser execution startup timeout',
            }
        return await LocalApiClient.api_call(c, method, path)

    c = _client(api)
    c._list_kernels = lambda: []
    with mock.patch.object(c, 'api_call', side_effect=always_timeout):
        with pytest.raises(StoreUnavailable) as ei:
            await c.open_store('kaaaaaa1')
    assert 'startup timeout' in str(ei.value)


async def test_auth_error_bypasses_the_decision(api):
    # A key problem means what it says — no process evidence needed.
    c = AdsPowerClient({'adspower_api_key': 'wrong'})
    c._port = api.port
    c._read_port = lambda: api.port
    c._proc = FakeProc()
    c._list_kernels = lambda: pytest.fail('must not look at processes')
    with pytest.raises(AccountMismatch):
        await c.open_store('kaaaaaa1')


# ── R4: ONE source for the platforms ───────────────────────────────


def test_platform_set_comes_from_the_vendor_info():
    assert ads_backend.ADSPOWER_VENDOR.platforms == frozenset({
        'windows',
        'mac',
        'wsl',
    })


# ── R5: rate-limit exhaustion is transient, not a permission error ──


async def test_rate_limit_exhaustion_is_transient(api):
    api.rate_limit_burst = 10_000
    c = _client(api)
    c.RATE_LIMIT_RETRIES = 1
    with pytest.raises(StoreUnavailable) as ei:
        await c.list_stores()
    assert '正忙' in str(ei.value)
    assert 'API 权限' not in str(ei.value)


# ── close_store cleans a Firefox window the API cannot stop ───────


async def test_close_store_kills_unstoppable_firefox_window(api):
    # FACTS §j: the API does not track a FlowerBrowser window
    # ("User_id is not open" on stop). After the ENGINE's kernel
    # refusal (P9) closes the session, our close_store must still
    # clean the window we opened — killing only this profile's tree.
    api.start_results['kaaaaaa2'] = 'firefox'
    c = _client(api)
    killed: list[int] = []
    c._list_kernels = lambda: [
        _kernel(701, 'flowerbrowser.exe', 'kaaaaaa2'),
        _kernel(702, 'sunbrowser.exe', 'kother999'),
    ]
    with mock.patch.object(c, '_kill_tree', side_effect=killed.append):
        await c.close_store('kaaaaaa2')
    assert killed == [701]


async def test_close_store_open_profile_leaves_kernels_alone(api):
    # A stop the API accepts means the vendor closed it — no process
    # evidence is touched.
    c = _client(api)
    called = []
    c._list_kernels = lambda: pytest.fail('must not list processes')
    with mock.patch.object(c, '_kill_tree', side_effect=called.append):
        await c.close_store('kaaaaaa1')
    assert called == []


async def test_kernel_updating_then_not_ready_keeps_downloading_hint(api):
    # R11b: the Windows GUI shape is updating → not ready → ready. Once
    # a download was SEEN, a timeout means slow, not absent — the hint
    # stays 「正在下载…稍后再试」, never 「后台不会自动下载」.
    api.start_results['kaaaaaa1'] = 'downloading'
    c = _client(api)
    c.KERNEL_TIMEOUT_S = 0.15
    seq = ['downloading', 'not_ready']

    async def rotating(method, path, json_body=None):
        api.start_results['kaaaaaa1'] = seq[0] if seq else 'not_ready'
        if len(seq) > 1:
            seq.pop(0)
        return await LocalApiClient.api_call(c, method, path)

    with mock.patch.object(c, 'api_call', side_effect=rotating):
        with pytest.raises(StoreUnavailable) as ei:
            await c.open_store('kaaaaaa1')
    assert '正在下载浏览器内核' in str(ei.value)
    assert '后台不会自动下载' not in str(ei.value)


# ── R19 (revised): env_was_open — errors RAISE, never None ──────────


async def test_env_was_open_active_inactive(api):
    # The documented status endpoint (FACTS §g): Active → True
    # (already open — never closed by us), Inactive → False (not
    # running → ours). A profile with no recorded answer → the closed
    # default.
    c = _client(api)
    api.active_answers['kaaaaaa1'] = 'Active'
    api.active_answers['kaaaaaa2'] = 'Inactive'
    assert await c.env_was_open('kaaaaaa1') is True
    assert await c.env_was_open('kaaaaaa2') is False
    assert await c.env_was_open('zzzz9999') is False


async def test_env_was_open_error_shapes_raise(api):
    # R19 REVISED: an error body (code -1) and a transport failure
    # RAISE the typed error — never None ("unknown ⇒ ours" is
    # reserved for reopens_hand_opened vendors, which AdsPower is
    # not: a None would let C4 close the user's window).
    c = _client(api)
    api.active_answers['kaaaaaa1'] = 'error'
    with pytest.raises(InjectError):
        await c.env_was_open('kaaaaaa1')
    c2 = _client(api)
    c2._read_port = lambda: 59999
    c2._port = 59999
    with pytest.raises(ClientNotRunning):
        await c2.env_was_open('kaaaaaa1')


async def test_env_was_open_unrecognized_status_raises(api):
    # A status word we do not know (a future vendor shape) RAISES
    # CdpUnreachable naming it — never a guess.
    c = _client(api)
    api.active_answers['kaaaaaa1'] = 'Starting'
    with pytest.raises(CdpUnreachable) as ei:
        await c.env_was_open('kaaaaaa1')
    assert 'Starting' in str(ei.value)


# ── R14/R14b: the start response itself names the kernel ────────────


async def test_flower159_start_refused_from_the_webdriver_path(api):
    # R14b: on flower_159 (today's default FlowerBrowser kernel) the
    # start SUCCEEDS and returns a WebDriver-BiDi endpoint whose
    # /json/version 404s — the engine would report a generic
    # CdpUnreachable. The REAL recorded response (native re-run
    # 2026-11-27) names the kernel in the webdriver path as
    # …\flower_159\geckodriver.exe, so the adapter refuses BEFORE the
    # engine fronts it. R14 missed this shape (it assumed
    # webdriver_flower_159\driver.exe).
    api.start_results['kaaaaaa2'] = 'flower159'
    c = _client(api)
    stopped: list[str] = []
    with mock.patch.object(c, 'close_store', side_effect=stopped.append):
        with pytest.raises(UnsupportedKernel) as ei:
            await c.open_store('kaaaaaa2')
    assert 'SunBrowser' in str(ei.value)
    assert 'Chrome 内核' in str(ei.value)
    assert stopped == ['kaaaaaa2']  # the profile was stopped


async def test_flower159_refusal_leaves_no_window(api):
    # R14b: the native re-run found a flower window LEFT BEHIND (11
    # processes) because the refusal never fired. With the real shape
    # matched, the refusal must also CLEAN UP: the local API cannot
    # stop a FlowerBrowser (stop answers "User_id is not open", FACTS
    # §j), so close_store kills exactly THIS profile's kernel tree.
    # Full path — close_store is NOT mocked.
    api.start_results['kaaaaaa2'] = 'flower159'
    c = _client(api)
    killed: list[int] = []
    c._list_kernels = lambda: [
        _kernel(801, 'flowerbrowser.exe', 'kaaaaaa2'),
        _kernel(802, 'sunbrowser.exe', 'kother999'),
    ]
    with mock.patch.object(c, '_kill_tree', side_effect=killed.append):
        with pytest.raises(UnsupportedKernel):
            await c.open_store('kaaaaaa2')
    assert killed == [801]  # only our window's tree


async def test_flower100_start_refused_from_the_webdriver_path(api):
    # The second recorded shape (FACTS §j, flower_100):
    # …\webdriver_flower_100\driver.exe. Same refusal, same cleanup.
    api.start_results['kaaaaaa2'] = 'flower100'
    c = _client(api)
    killed: list[int] = []
    c._list_kernels = lambda: [_kernel(811, 'flowerbrowser.exe', 'kaaaaaa2')]
    with mock.patch.object(c, '_kill_tree', side_effect=killed.append):
        with pytest.raises(UnsupportedKernel) as ei:
            await c.open_store('kaaaaaa2')
    assert 'SunBrowser' in str(ei.value)
    assert killed == [811]


async def test_flower159_twist_the_sunbrowser_shape_passes(api):
    # The SunBrowser start response (chromedriver path) is unaffected.
    c = _client(api)
    opened = await c.open_store('kaaaaaa1')
    assert opened.cdp_port == api.cdp_port


async def test_start_names_firefox_shape():
    # Both shapes recorded live, plus the negatives (SunBrowser, a
    # bare geckodriver, macOS forward slashes).
    c = AdsPowerClient({})
    # flower_159 (today's default) — the REAL recorded path
    # (native re-run 2026-11-27):
    assert (
        c.start_names_firefox({
            'data': {
                'webdriver': (
                    'C:\\Users\\<user>\\AppData\\Roaming'
                    '\\adspower_global\\cwd_global\\flower_159'
                    '\\geckodriver.exe'
                )
            }
        })
        is True
    )
    # flower_100 — the FACTS §j recorded path:
    assert (
        c.start_names_firefox({
            'data': {
                'webdriver': (
                    'C:\\Users\\<user>\\AppData\\Roaming'
                    '\\adspower_global\\cwd_global'
                    '\\webdriver_flower_100\\driver.exe'
                )
            }
        })
        is True
    )
    # A geckodriver file alone (no flower directory) is Firefox too:
    assert (
        c.start_names_firefox({
            'data': {'webdriver': 'C:\\kernels\\geckodriver.exe'}
        })
        is True
    )
    # macOS-style forward slashes:
    assert (
        c.start_names_firefox({
            'data': {
                'webdriver': (
                    '/Users/<user>/Library/Application Support/'
                    'adspower_global/cwd_global/flower_159/geckodriver'
                )
            }
        })
        is True
    )
    # SunBrowser (chromedriver) is NOT Firefox:
    assert (
        c.start_names_firefox({
            'data': {'webdriver': 'x\\chrome_156\\chromedriver.exe'}
        })
        is False
    )
    assert c.start_names_firefox({'data': {}}) is False
    assert c.start_names_firefox(None) is False
