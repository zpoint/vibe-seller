"""BitBrowser adapter tests — the recorded live shapes, placeholdered.

Every response family the Stage-1 probe captured
(``~/bitbrowser-glm/probes/`` on the dev machine) is served by a fake
local API (aiohttp) with PLACEHOLDERS ONLY (``demo-store-*``,
``100000000001``, ``203.0.113.10``); list rows carry sentinel site
credentials proving they never leave the client. Each behaviour pairs
with a twist proving the opposite shape, and the after_open tests fake
the kernel's CDP endpoints (/json/list, /json/version + the browser WS)
to pin the workbench-tab hygiene. The shared fakes live in
``bitbrowser_test_support`` (not a test module).
"""

from dataclasses import asdict
import inspect
import json
import logging

import pytest

from app.ai import vendor_guard
import app.browser.adapters.bitbrowser as bb
from app.browser.adapters.bitbrowser import (
    BITBROWSER_VENDOR,
    BitBrowserBackend,
    BitBrowserClient,
    platform_hostname,
    port_from_address,
    read_client_config,
)
import app.browser.adapters.bitbrowser_launch as bb_launch
from app.browser.inject.client import OpenedStore
from app.browser.inject.errors import (
    ApiNotEnabled,
    ClientNotRunning,
    LoginRequired,
    StoreNotFound,
    StoreUnavailable,
    UnsupportedKernel,
    UnsupportedPlatform,
)
from tests.unit.test_browser.bitbrowser_test_support import (
    ROWS,
    SENTINELS,
    FakeCDP,
    _async_return,
    _client,
    _default_routes,
    _page,
    _run_after_open,
    _serve,
    _servers,
    cleanup,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
async def _teardown():
    yield
    await cleanup()


# ── list / pagination / request shapes ───────────────────────────────


async def test_list_parses_rows_into_bare_store_refs():
    server = await _serve(_default_routes())
    client = _client(server.port)
    refs = await client.list_stores()
    assert [r.ref for r in refs] == ['100000000001', '100000000002']
    assert [r.name for r in refs] == [
        '#1 demo-store-one',
        '#2 demo-store-two',
    ]
    assert [r.platform for r in refs] == ['amazon.com', 'facebook.com']


async def test_list_marks_firefox_rows_unavailable():
    """The kernel greying: anything not 'chrome' is greyed with a
    reason — from the LIST alone, without opening anything."""
    server = await _serve(_default_routes())
    client = _client(server.port)
    refs = await client.list_stores()
    assert refs[0].available is True
    assert refs[0].unavailable_reason == ''
    assert refs[1].available is False
    assert refs[1].unavailable_reason == 'unsupported_kernel'
    assert refs[1].unavailable_detail == 'firefox'


async def test_list_twist_a_chrome_only_list_is_fully_available():
    """Negative control: the greying above is the coreProduct field,
    not the row shape — a chrome row stays selectable."""
    rows = [dict(ROWS[0])]
    server = await _serve({
        ('POST', '/browser/list'): (
            200,
            {
                'success': True,
                'data': {
                    'list': rows,
                    'totalNum': 1,
                    'page': 0,
                    'pageSize': 100,
                },
            },
        )
    })
    client = _client(server.port)
    refs = await client.list_stores()
    assert refs[0].available is True


async def test_list_requests_are_post_json_with_the_documented_bodies():
    server = await _serve(_default_routes())
    client = _client(server.port)
    refs = await client.list_stores()
    opened = await client.open_store(refs[0].ref)
    await client.close_store(refs[0].ref)
    lists = [r for r in server.requests if r['path'] == '/browser/list']
    opens = [r for r in server.requests if r['path'] == '/browser/open']
    closes = [r for r in server.requests if r['path'] == '/browser/close']
    details = [r for r in server.requests if r['path'] == '/browser/detail']
    assert lists == [
        {
            'method': 'POST',
            'path': '/browser/list',
            'json': {'page': 0, 'pageSize': 100},
            'headers': lists[0]['headers'],
        },
    ]
    assert len(details) == 1 and details[0]['json'] == {'id': '100000000001'}
    assert opens[0]['json'] == {'id': '100000000001', 'queue': True}
    assert closes[0]['json'] == {'id': '100000000001'}
    assert (opened.cdp_host, opened.cdp_port) == ('127.0.0.1', 54321)


async def test_list_follows_totalnum_pagination():
    """pageSize forced to 1: 2 rows → 2 pages, in order."""
    server = await _serve(_default_routes())
    client = _client(server.port)
    client.page_size = 1
    refs = await client.list_stores()
    assert [r.ref for r in refs] == ['100000000001', '100000000002']
    pages = [
        r['json']['page']
        for r in server.requests
        if r['path'] == '/browser/list'
    ]
    assert pages == [0, 1]


async def test_list_twist_without_pagination_only_page_one():
    """Negative control: the 2-row result above is pagination, not
    luck — disabling next_page yields only page 0's row."""
    server = await _serve(_default_routes())
    client = _client(server.port)
    client.page_size = 1
    client.next_page = lambda payload, page: None
    refs = await client.list_stores()
    assert [r.ref for r in refs] == ['100000000001']


# ── the logged-out / error taxonomy ──────────────────────────────────


async def test_logged_out_is_login_required():
    server = await _serve({
        ('POST', '/browser/list'): (
            200,
            {'success': False, 'msg': 'token 失效，请检查登录状态'},
        )
    })
    client = _client(server.port)
    with pytest.raises(LoginRequired):
        await client.list_stores()


async def test_logged_out_twist_a_real_empty_list_is_not_login_required():
    """Negative control: an ANSWERING client with zero profiles is an
    empty picker, never a login error."""
    server = await _serve({
        ('POST', '/browser/list'): (
            200,
            {'success': True, 'data': {'list': [], 'totalNum': 0}},
        )
    })
    client = _client(server.port)
    assert await client.list_stores() == []


async def test_open_unknown_ref_is_store_not_found():
    server = await _serve({
        ('POST', '/browser/detail'): (
            200,
            {'success': False, 'msg': '服务调用成功，但没有找到相应数据！'},
        )
    })
    client = _client(server.port)
    with pytest.raises(StoreNotFound):
        await client.open_store('deadbeef')


async def test_auth_failure_is_api_not_enabled():
    """HTTP 403 (the recorded 鉴权 body) → API_NOT_ENABLED with the
    BitBrowser recovery hint."""
    server = await _serve({
        ('POST', '/browser/list'): (
            403,
            {
                'success': False,
                'msg': 'header.x-api-key验证失败，请检查比特浏览器软件是否'
                '正确设置Local API Token，接口调用是否正确传入'
                'x-api-key',
            },
        )
    })
    client = _client(server.port)
    with pytest.raises(ApiNotEnabled) as exc:
        await client.list_stores()
    assert '鉴权控制' in str(exc.value)


async def test_other_business_failures_are_store_unavailable():
    """Any other success:false carries the vendor's own message — the
    kernel-download failure must NOT become UNSUPPORTED_KERNEL."""
    server = await _serve({
        ('POST', '/browser/detail'): (
            200,
            {'success': False, 'msg': '内核更新失败，请重新启动客户端再打开'},
        )
    })
    client = _client(server.port)
    with pytest.raises(StoreUnavailable) as exc:
        await client.open_store('100000000001')
    assert '内核更新失败' in str(exc.value)
    assert not isinstance(exc.value, UnsupportedKernel)


async def test_not_running_is_client_not_running(monkeypatch):
    """Port 1: nothing listens (an unbound loopback port on WSL hangs
    instead of refusing — the connect timeout must bound it). The
    launch seam is patched and the poll shortened: a unit test must
    never start or locate the REAL client, and must not sit through
    the 30 s launch poll."""

    async def _no_launch():
        raise ClientNotRunning('not launched in tests')

    client = _client(1)
    client.connect_timeout_s = 0.3
    client._LAUNCH_POLL_ATTEMPTS = 1
    client._LAUNCH_POLL_DELAY_S = 0.05
    client.launch_client = _no_launch
    with pytest.raises(ClientNotRunning):
        await client.ensure_running()


# ── the kernel detail-check at open ──────────────────────────────────


async def test_open_refuses_non_chrome_kernel_before_opening():
    """The detail check runs BEFORE /browser/open — a Firefox profile
    is refused without the open call ever happening (its open shape is
    unverified and must not be relied on)."""
    server = await _serve(_default_routes())
    client = _client(server.port)
    with pytest.raises(UnsupportedKernel) as exc:
        await client.open_store('100000000002')
    assert 'Chrome' in str(exc.value)
    assert not [r for r in server.requests if r['path'] == '/browser/open']


async def test_open_twist_a_chrome_profile_passes_the_detail_check():
    server = await _serve(_default_routes())
    client = _client(server.port)
    opened = await client.open_store('100000000001')
    assert opened.cdp_port == 54321


# ── secrets never leave the client ───────────────────────────────────


async def test_site_credentials_never_reach_store_refs_or_logs(caplog):
    server = await _serve(_default_routes())
    client = _client(server.port)
    with caplog.at_level(logging.DEBUG, logger='app.browser'):
        refs = await client.list_stores()
        await client.open_store(refs[0].ref)
        await client.close_store(refs[0].ref)
    rows = [asdict(ref) for ref in refs]
    for row in rows:
        assert set(row) <= {
            'name',
            'platform',
            'remote_env',
            'ref',
            'available',
            'unavailable_reason',
            'unavailable_detail',
        }
    for sentinel in SENTINELS:
        assert sentinel not in str(rows), 'secret leaked into StoreRef'
        assert sentinel not in caplog.text, 'secret leaked into logs'


async def test_secret_check_twist_the_capture_sees_a_planted_leak(caplog):
    """Negative control: the clean result above means the client is
    silent, not that the assertion is blind."""
    with caplog.at_level(logging.DEBUG, logger='app.browser'):
        logging.getLogger('app.browser').info('planted leak %s', SENTINELS[0])
    assert SENTINELS[0] in caplog.text


# ── config.json discovery / auth headers ─────────────────────────────


def _patch_config_seams(monkeypatch, tmp_path):
    """Point BOTH platform config-path seams at the fixture dir.

    ``read_client_config`` reads the macOS data dir when the test host
    is a mac and ``%APPDATA%`` (env or PowerShell) otherwise. A test
    that patches only the Windows seam therefore reads the REAL
    ``~/Library/Application Support/BitBrowser/config.json`` when the
    suite runs on a mac — observed live: the garbage-fixture and
    missing-file tests failed with the machine's own port 54345, and
    the token test saw the machine's ``enableAuth: false``. Patching
    both seams keeps every config.json test hermetic on windows, wsl
    AND mac.
    """
    cfg_dir = tmp_path / 'BitBrowser'
    monkeypatch.setattr(
        'app.browser.adapters.bitbrowser_launch._windows_appdata',
        _async_return(str(tmp_path)),
    )
    monkeypatch.setattr(
        'app.browser.adapters.bitbrowser_launch._mac_config_path',
        lambda: str(cfg_dir / 'config.json'),
    )
    return cfg_dir


async def test_discover_port_reads_localserveraddress(tmp_path, monkeypatch):
    cfg_dir = _patch_config_seams(monkeypatch, tmp_path)
    cfg_dir.mkdir()
    (cfg_dir / 'config.json').write_text(
        json.dumps({
            'localServerAddress': 'http://127.0.0.1:54345',
            'enableAuth': False,
        }),
        encoding='utf-8',
    )
    client = BitBrowserClient({})
    assert await client.discover_port() == 54345
    assert await client.auth_headers() == {}


async def test_discover_port_ignores_non_loopback_and_garbage(
    tmp_path, monkeypatch
):
    cfg_dir = _patch_config_seams(monkeypatch, tmp_path)
    cfg_dir.mkdir()
    (cfg_dir / 'config.json').write_text('not json at all{', encoding='utf-8')
    client = BitBrowserClient({})
    # A garbage file → no discovery result → the default port is used.
    assert await client.discover_port() is None
    (cfg_dir / 'config.json').write_text(
        json.dumps({
            'localServerAddress': 'http://203.0.113.10:9999',
        }),
        encoding='utf-8',
    )
    # A non-loopback host is not ours to dial.
    assert await client.discover_port() is None


async def test_discover_port_missing_file(tmp_path, monkeypatch):
    _patch_config_seams(monkeypatch, tmp_path)
    client = BitBrowserClient({})
    assert await client.discover_port() is None
    assert await read_client_config() == {}


async def test_auth_headers_sent_only_when_enable_auth(tmp_path, monkeypatch):
    cfg_dir = _patch_config_seams(monkeypatch, tmp_path)
    cfg_dir.mkdir()
    (cfg_dir / 'config.json').write_text(
        json.dumps({
            'localServerAddress': 'http://127.0.0.1:54345',
            'enableAuth': True,
            'authToken': 'tok-placeholder-123',
        }),
        encoding='utf-8',
    )
    client = BitBrowserClient({})
    assert await client.auth_headers() == {'x-api-key': 'tok-placeholder-123'}
    # A reset token (enableAuth off) sends nothing — on a FRESH client
    # (per-instance cache; the re-read story is the tests below).
    (cfg_dir / 'config.json').write_text(
        json.dumps({
            'localServerAddress': 'http://127.0.0.1:54345',
            'enableAuth': False,
            'authToken': 'tok-placeholder-123',
        }),
        encoding='utf-8',
    )
    client2 = BitBrowserClient({})
    assert await client2.auth_headers() == {}


async def test_config_read_once_then_cached(tmp_path, monkeypatch):
    """One file read serves every call (a /mnt read on WSL on a mac
    is not free): N calls, exactly 1 read."""
    cfg_dir = _patch_config_seams(monkeypatch, tmp_path)
    cfg_dir.mkdir()
    (cfg_dir / 'config.json').write_text(
        json.dumps({'localServerAddress': 'http://127.0.0.1:54345'}),
        encoding='utf-8',
    )
    reads = []
    orig_read = bb.read_client_config

    async def _counting_read():
        reads.append(1)
        return await orig_read()

    monkeypatch.setattr(bb, 'read_client_config', _counting_read)
    client = bb.BitBrowserClient({})
    for _ in range(5):
        await client.auth_headers()
    await client.discover_port()
    assert len(reads) == 1


async def test_config_twist_without_the_cache_every_call_reads(
    monkeypatch,
):
    """Negative control: the single read above is the cache, not the
    read path deduplicating — the raw read really runs per call."""
    reads = []

    async def _counting_read():
        reads.append(1)
        return {'enableAuth': False}

    monkeypatch.setattr(bb, 'read_client_config', _counting_read)
    for _ in range(3):
        await bb.read_client_config()
    assert len(reads) == 3


def test_port_from_address_shapes():
    assert port_from_address('http://127.0.0.1:54345') == 54345
    assert port_from_address('http://localhost:59999') == 59999
    assert port_from_address('http://203.0.113.10:9999') is None
    assert port_from_address('not a url') is None
    assert port_from_address(None) is None
    assert port_from_address('http://127.0.0.1:') is None


def test_platform_hostname_shapes():
    assert platform_hostname('https://www.amazon.com/') == 'amazon.com'
    assert platform_hostname('https://facebook.com') == 'facebook.com'
    assert platform_hostname('') == ''
    assert platform_hostname(None) == ''


async def test_after_open_closes_workbench_and_activates_the_store_tab():
    cdp = await _run_after_open([
        _page('https://console.bitbrowser.net/?id=x&locale=zh', 'wb1'),
        _page('https://www.amazon.com/', 'store1'),
    ])
    closed = [
        tid
        for method, tid in (
            (m, p.get('targetId'))
            for m, p in cdp.commands
            if m == 'Target.closeTarget'
        )
    ]
    activated = [
        tid
        for method, tid in (
            (m, p.get('targetId'))
            for m, p in cdp.commands
            if m == 'Target.activateTarget'
        )
    ]
    assert closed == ['wb1']  # only the workbench tab
    assert activated == ['store1']  # the store tab is focused


async def test_after_open_never_closes_the_last_tab():
    """Only a workbench tab and nothing else: closing it would close
    the browser — it must stay."""
    cdp = await _run_after_open([
        _page('https://console.bitbrowser.net/?id=x', 'wb1'),
    ])
    assert not [c for c in cdp.commands if c[0] == 'Target.closeTarget']


async def test_after_open_twist_extension_tabs_are_not_pages():
    """Negative control: only 'page' targets count as the other tab —
    service workers / browser UI must not green-light the close."""
    cdp = FakeCDP([
        _page('https://console.bitbrowser.net/?id=x', 'wb1'),
        {
            'type': 'service_worker',
            'url': 'chrome-extension://x/service-worker.js',
            'targetId': 'sw1',
        },
    ])
    await cdp.start()
    _servers.append(cdp)

    client = _client(1)
    client.after_open_delay_s = 0.05
    client.after_open_deadline_s = 0.2
    await client.after_open(OpenedStore('127.0.0.1', cdp.port))
    assert not [c for c in cdp.commands if c[0] == 'Target.closeTarget']


# ── the spec ─────────────────────────────────────────────────────────


def test_spec_declares_the_sign_in_once_local_api_shape():
    assert BITBROWSER_VENDOR.key == 'bitbrowser'
    assert BITBROWSER_VENDOR.account_cardinality == 'single'
    assert [(f.key, f.secret) for f in BITBROWSER_VENDOR.credential_fields] == [
        ('username', False),
        ('password', True),
    ]
    assert BITBROWSER_VENDOR.lists_profiles is True
    assert BITBROWSER_VENDOR.picker_kind == 'generic'
    assert BITBROWSER_VENDOR.client_kind == 'local_api'
    assert sorted(BITBROWSER_VENDOR.platforms) == ['mac', 'windows', 'wsl']
    assert BITBROWSER_VENDOR.win_app is not None
    assert BITBROWSER_VENDOR.client_path_setting == ('bitbrowser_client_path')
    meta = BITBROWSER_VENDOR.public_metadata()
    assert meta['display_name_zh'] == '比特浏览器'
    assert [f['key'] for f in meta['credential_fields']] == [
        'username',
        'password',
    ]


def test_rate_limit_spacing_is_the_documented_budget():
    """min_interval_s = 0.2 keeps us under the recorded 10 req/s."""
    client = BitBrowserClient({})
    assert 0 < client.min_interval_s < 0.5


# ── ensure_running / launch seam ─────────────────────────────────────


async def test_ensure_running_launches_when_the_api_is_down(monkeypatch):
    """Down → the launch seam RUNS → the API comes up → running. The
    poll delay is shrunk so the launch poll costs milliseconds, and
    the LAUNCH ITSELF is asserted (not just that probing happened)."""
    server = await _serve(_default_routes())
    client = _client(server.port)
    client._LAUNCH_POLL_DELAY_S = 0.05
    calls: list[bool] = []
    launched: list[str] = []

    async def _probe_first_fail_then_ok() -> bool:
        calls.append(True)
        return len(calls) > 1  # first probe fails, later ones succeed

    monkeypatch.setattr(client, '_probe_running', _probe_first_fail_then_ok)
    monkeypatch.setattr(client, 'launch_client', _record_launch(launched))
    await client.ensure_running()
    assert launched == ['launched']  # the launch really happened
    assert calls  # probed, launched, probed again


def _record_launch(sink: list):
    async def _launch():
        sink.append('launched')
        return None

    return _launch


async def test_ensure_running_not_found_is_client_not_running(monkeypatch):
    """Nothing on disk (launch seam raises the install-hint error) →
    CLIENT_NOT_RUNNING, surfaced immediately."""
    server = await _serve({})
    client = _client(server.port)

    async def _probe_fail() -> bool:
        return False

    async def _not_found():
        raise ClientNotRunning('could not find the BitBrowser client')

    monkeypatch.setattr(client, '_probe_running', _probe_fail)
    monkeypatch.setattr(client, 'launch_client', _not_found)
    with pytest.raises(ClientNotRunning) as exc:
        await client.ensure_running()
    assert 'install' in str(exc.value) or 'find' in str(exc.value)


async def test_ensure_running_twist_an_answering_api_never_launches(
    monkeypatch,
):
    """Negative control: a running client (even one answering with
    business errors) must not trigger the launch path."""
    server = await _serve({
        ('POST', '/health'): (
            200,
            {'success': True, 'data': 'the server is running good.'},
        )
    })
    client = _client(server.port)
    launched: list[str] = []
    monkeypatch.setattr(client, 'launch_client', _record_launch(launched))
    await client.ensure_running()
    assert launched == []


# ── macOS code paths (written now; 'mac' is not in spec.platforms yet) ─


async def test_mac_config_read_uses_the_macos_data_dir(monkeypatch, tmp_path):
    """On mac the config comes from ~/Library/Application Support/
    BitBrowser — never the Windows %APPDATA% flow."""
    cfg_file = tmp_path / 'config.json'
    # A DISTINCT port: the assertion can only pass through the mac
    # path, never by falling back to any default.
    cfg_file.write_text(
        json.dumps({
            'localServerAddress': 'http://127.0.0.1:54346',
        }),
        encoding='utf-8',
    )
    monkeypatch.setattr(bb_launch, 'get_platform', lambda: 'mac')
    monkeypatch.setattr(bb_launch, '_mac_config_path', lambda: str(cfg_file))
    client = bb.BitBrowserClient({})
    assert await client.discover_port() == 54346
    # A fresh install's file has no enableAuth key → auth OFF.
    assert await client.auth_headers() == {}


async def test_mac_config_twist_missing_file_is_auth_off_default_port(
    monkeypatch,
    tmp_path,
):
    """Negative control: no config at all on mac → no discovery result
    (the default port applies) and no auth header."""
    monkeypatch.setattr(bb_launch, 'get_platform', lambda: 'mac')
    monkeypatch.setattr(
        bb_launch,
        '_mac_config_path',
        lambda: str(tmp_path / 'nonexistent' / 'config.json'),
    )
    client = bb.BitBrowserClient({})
    assert await client.discover_port() is None
    assert await client.auth_headers() == {}


async def test_windows_platform_still_reads_appdata(monkeypatch, tmp_path):
    """The platform switch is real: on windows/wsl the SAME client
    reads the %APPDATA% file, not the mac data dir."""
    cfg_dir = tmp_path / 'BitBrowser'
    cfg_dir.mkdir()
    (cfg_dir / 'config.json').write_text(
        json.dumps({
            'localServerAddress': 'http://127.0.0.1:54346',
            'enableAuth': True,
            'authToken': 'tok-mac-should-not-see',
        }),
        encoding='utf-8',
    )
    monkeypatch.setattr(bb_launch, 'get_platform', lambda: 'wsl')
    monkeypatch.setattr(
        bb_launch, '_windows_appdata', _async_return(str(tmp_path))
    )
    client = bb.BitBrowserClient({})
    assert await client.discover_port() == 54346
    assert await client.auth_headers() == {
        'x-api-key': 'tok-mac-should-not-see'
    }


def test_mac_descriptor_values_and_kernel_bundle_exclusion():
    """The client bundle is the CHINESE-named 比特浏览器.app with bundle
    id com.bitnet.bitbrowser; the profile KERNEL is an English-named
    BitBrowser.app / BitFox.app inside Chrome-bin/Firefox-bin and must
    never be matched as the client."""
    spec = bb_launch.mac_app_descriptor()
    assert spec.bundle_ids == ('com.bitnet.bitbrowser',)
    assert spec.app_names == ('比特浏览器.app',)
    # The kernel bundles are English-named — not in the client's names.
    assert 'BitBrowser.app' not in bb_launch._MAC_APP_NAMES
    assert 'BitFox.app' not in bb_launch._MAC_APP_NAMES


async def test_mac_launch_refuses_a_kernel_bundle_path(monkeypatch):
    """A saved client path pointing at Chrome-bin/...BitBrowser.app is
    a kernel, not the client — refused with a clear error, never
    launched."""
    monkeypatch.setattr(bb, 'get_platform', lambda: 'mac')
    client = bb.BitBrowserClient({
        'client_path': '~/Library/Application Support/BitBrowser/'
        'Chrome-bin/148/win148/BitBrowser.app',
    })
    with pytest.raises(ClientNotRunning) as exc:
        await client.launch_client()
    assert 'kernel bundle' in str(exc.value)


def test_spec_platforms_include_mac_after_the_live_run():
    """'mac' joined spec.platforms after the native-macOS live run
    passed (the overnight matrix: auto-launch via the shared locator,
    open/close, kill recovery, the MiniMax acceptance task). Removing
    it must fail here — the platform is offered, not just tolerated."""
    assert 'mac' in BITBROWSER_VENDOR.platforms
    assert sorted(BITBROWSER_VENDOR.platforms) == ['mac', 'windows', 'wsl']


# ── addendum-4 review fixes ──────────────────────────────────────────


async def test_logged_out_on_the_open_path_is_login_required():
    """A logged-out /browser/detail (or open) answer surfaces as
    LOGIN_REQUIRED, never as a store problem: the base's shared
    response path checks is_logged_out on EVERY call (the vendor's
    own extra endpoints included — this kernel check is one)."""
    server = await _serve({
        ('POST', '/browser/detail'): (
            200,
            {'success': False, 'msg': 'token 失效，请检查登录状态'},
        )
    })
    client = _client(server.port)
    with pytest.raises(LoginRequired):
        await client.open_store('100000000001')
    # The built-in open path shares the same response path (the base's
    # api_call checks is_logged_out before map_error on every call —
    # pinned by PR B's own suite).


async def test_logged_out_twist_a_real_store_error_is_not_login():
    """Negative control: a genuine business failure on the open path
    stays STORE_UNAVAILABLE — the guard matches the token message, not
    every failure."""
    server = await _serve({
        ('POST', '/browser/detail'): (
            200,
            {'success': False, 'msg': '内核更新失败，请重新启动客户端再打开'},
        )
    })
    client = _client(server.port)
    with pytest.raises(StoreUnavailable):
        await client.open_store('100000000001')


async def test_open_detail_non_dict_payload_is_store_unavailable():
    """A text/HTML body from /browser/detail (a proxy in front of the
    port, a broken client) must not raise AttributeError."""
    server = await _serve({
        ('POST', '/browser/detail'): (200, '<html>gateway error</html>'),
    })
    client = _client(server.port)
    with pytest.raises(StoreUnavailable):
        await client.open_store('100000000001')


async def test_open_detail_non_dict_twist_a_dict_error_still_maps():
    """Negative control: the guard above is about SHAPE — a dict error
    payload still maps through the taxonomy, not to the generic
    StoreUnavailable."""
    server = await _serve({
        ('POST', '/browser/detail'): (
            200,
            {'success': False, 'msg': '服务调用成功，但没有找到相应数据！'},
        )
    })
    client = _client(server.port)
    with pytest.raises(StoreNotFound):
        await client.open_store('gone')


async def test_platform_gate_derives_from_the_spec(monkeypatch):
    """The backend's platform gate reads the spec (adding a platform
    there is the ONLY edit) and names every supported platform — no
    second platform list in the module. 'linux' is the unsupported
    stand-in (mac IS supported since the live run)."""

    async def _fail(cfg):
        raise AssertionError('start must not run on an unsupported platform')

    monkeypatch.setattr(bb, 'get_platform', lambda: 'linux')
    backend = BitBrowserBackend(lambda cfg: None)
    with pytest.raises(UnsupportedPlatform) as exc:
        await backend.start({'browser_ref': 'x'})
    # The message names the spec's platforms generically.
    assert 'windows' in str(exc.value) and 'wsl' in str(exc.value)
    assert 'mac' in str(exc.value)
    # Structural: the gate carries no platform list of its own.
    src = inspect.getsource(BitBrowserBackend.start)
    assert '_SUPPORTED_PLATFORMS' not in src
    assert 'BITBROWSER_VENDOR.platforms' in src


async def test_after_open_activates_an_http_page_not_an_extension():
    """An extension page listed FIRST must not be the landing tab: the
    store's http(s) page is activated."""
    cdp = await _run_after_open([
        _page('chrome-extension://abc/popup.html', 'ext1'),
        _page('https://www.amazon.com/', 'store1'),
    ])
    activated = [
        p.get('targetId')
        for m, p in cdp.commands
        if m == 'Target.activateTarget'
    ]
    assert activated == ['store1']


async def test_after_open_prefers_the_profile_platform_host():
    """When open_store recorded the profile's platform, its tab wins
    over another http(s) page (e.g. a second window the user opened)."""
    server = await _serve(_default_routes())
    client = _client(server.port)
    await client.open_store('100000000001')  # records the platform
    assert client._platform_host == 'amazon.com'
    cdp = FakeCDP([
        _page('https://www.example.com/', 'other'),
        _page('https://www.amazon.com/', 'store1'),
    ])
    await cdp.start()
    _servers.append(cdp)
    await client.after_open(OpenedStore('127.0.0.1', cdp.port))
    activated = [
        p.get('targetId')
        for m, p in cdp.commands
        if m == 'Target.activateTarget'
    ]
    assert activated == ['store1']


# ── after_open × expects_workbench (the workbench race, from design) ─


async def _after_open_on(pages, listings=None, *, expects_workbench=True):
    """Run after_open against a FakeCDP (optionally scripted per
    listing) and return the fake for command assertions."""
    cdp = FakeCDP(pages=pages, listings=listings)
    await cdp.start()
    _servers.append(cdp)
    client = _client(1)
    client.after_open_delay_s = 0.05
    client.after_open_deadline_s = 0.4
    client._expects_workbench = expects_workbench
    await client.after_open(OpenedStore('127.0.0.1', cdp.port))
    return cdp


def _closed_ids(cdp) -> list:
    return [
        p.get('targetId') for m, p in cdp.commands if m == 'Target.closeTarget'
    ]


def _activated_ids(cdp) -> list:
    return [
        p.get('targetId')
        for m, p in cdp.commands
        if m == 'Target.activateTarget'
    ]


async def test_after_open_expected_and_late_workbench_is_closed():
    """Expected workbench appearing only on the THIRD listing (the
    race: the platform tab is created first) — still closed, store tab
    activated. Fails on the pre-fix code (89c50109), which concluded
    'already clean' from the first listing."""
    cdp = await _after_open_on(
        pages=[],
        listings=[
            [_page('https://www.amazon.com/', 'store1')],
            [_page('https://www.amazon.com/', 'store1')],
            [
                _page('https://www.amazon.com/', 'store1'),
                _page('https://console.bitbrowser.net/?id=x', 'wb1'),
            ],
        ],
    )
    assert _closed_ids(cdp) == ['wb1']
    assert _activated_ids(cdp) == ['store1']


async def test_after_open_twist_disable_activates_immediately():
    """Negative control + the disable design: a workbench-free profile
    (workbench='disable') activates its store tab on the FIRST listing
    and makes no further listings — no waiting for a tab that never
    comes."""
    cdp = await _after_open_on(
        pages=[_page('https://www.amazon.com/', 'store1')],
        expects_workbench=False,
    )
    assert cdp.listing_hits == 1
    assert not _closed_ids(cdp)
    assert _activated_ids(cdp) == ['store1']


async def test_after_open_expected_but_never_appearing_ends_at_deadline():
    """A workbench that never appears (e.g. the user closed it in a
    hand-opened profile): the deadline ends the wait with one warning
    and the store tab is still activated."""
    cdp = await _after_open_on(
        pages=[_page('https://www.amazon.com/', 'store1')],
        expects_workbench=True,
    )
    assert cdp.listing_hits > 1  # it really waited
    assert not _closed_ids(cdp)
    assert _activated_ids(cdp) == ['store1']


async def test_after_open_twist_lone_workbench_tab_is_never_closed():
    """Negative control for the last-tab rule: only a workbench tab
    and no store tab ever — closing it would close the browser."""
    cdp = await _after_open_on(
        pages=[_page('https://console.bitbrowser.net/?id=x', 'wb1')],
        expects_workbench=True,
    )
    assert not _closed_ids(cdp)
    assert not _activated_ids(cdp)


# ── the device-kick logged-out shape (addendum 16) ───────────────────


@pytest.mark.parametrize(
    'path', ['/browser/list', '/browser/detail', '/browser/open']
)
async def test_device_kick_is_login_required_on_every_endpoint(path):
    """One BitBrowser account = one signed-in device: signing in on
    another computer kicks this client, which answers HTTP 200
    {"success":false,"msg":"Login out！"} (English, FULLWIDTH !) — a
    SECOND logged-out shape beside 「token 失效…」. Whatever endpoint it
    surfaces on, it must read as LOGIN_REQUIRED, never a store error.
    """
    shape = (200, {'success': False, 'msg': 'Login out！'})
    routes = {
        ('POST', '/browser/list'): shape,
        ('POST', '/browser/detail'): shape,
        ('POST', '/browser/open'): shape,
        ('POST', '/health'): (200, {'success': True}),
    }
    server = await _serve(routes)
    client = _client(server.port)
    if path == '/browser/list':
        with pytest.raises(LoginRequired):
            await client.list_stores()
    else:
        with pytest.raises(LoginRequired):
            await client.open_store('100000000001')


async def test_device_kick_twist_fails_on_the_pre_fix_marks():
    """Negative control: the kick shape is matched by the marks tuple
    — the OLD marks (without 'Login out！') would let it fall through
    to STORE_UNAVAILABLE, the very bug the fix removes. Pin that the
    tuple carries the kick mark, so removing it fails here."""
    assert 'Login out！' in bb._TOKEN_EXPIRED_MARKS
    # And the old tuple really did miss it (the pre-fix behaviour):
    old_marks = ('token 失效', 'token is invalid')
    kicked = {'success': False, 'msg': 'Login out！'}
    assert not any(m in kicked['msg'] for m in old_marks)


async def test_device_kick_twist_a_store_error_is_not_logged_out():
    """Negative control: the marks stay marks — a genuine store error
    (success:false with another message) is NOT logged out."""
    server = await _serve({
        ('POST', '/browser/detail'): (
            200,
            {'success': False, 'msg': '内核更新失败，请重新启动客户端再打开'},
        )
    })
    client = _client(server.port)
    with pytest.raises(StoreUnavailable):
        await client.open_store('100000000001')


# ── review round 1: the 403 gate + the list-path guard ──────────────


async def test_403_with_the_api_key_mark_is_api_not_enabled():
    """Only the RECORDED 鉴权控制 body (the x-api-key mark) maps to
    API_NOT_ENABLED with the plain check-the-toggle hint."""
    server = await _serve({
        ('POST', '/browser/list'): (
            403,
            {
                'success': False,
                'msg': 'header.x-api-key验证失败，请检查比特浏览器软件是否'
                '正确设置Local API Token，接口调用是否正确传入'
                'x-api-key',
            },
        )
    })
    client = _client(server.port)
    with pytest.raises(ApiNotEnabled) as exc:
        await client.list_stores()
    assert '鉴权控制' in str(exc.value)


async def test_403_twist_any_other_403_is_store_unavailable():
    """Negative control: a 403 WITHOUT the x-api-key mark (a proxy, a
    wrong route) is not the auth gate — it carries its message as
    STORE_UNAVAILABLE."""
    server = await _serve({
        ('POST', '/browser/list'): (
            403,
            {'success': False, 'msg': 'Forbidden by proxy'},
        )
    })
    client = _client(server.port)
    with pytest.raises(StoreUnavailable) as exc:
        await client.list_stores()
    assert 'proxy' in str(exc.value)
    assert not isinstance(exc.value, ApiNotEnabled)


async def test_list_non_dict_data_is_store_unavailable():
    """A text/HTML body on /browser/list (same guard as the open path)
    must not raise AttributeError on payload.get."""
    server = await _serve({
        ('POST', '/browser/list'): (200, '<html>gateway error</html>'),
    })
    client = _client(server.port)
    with pytest.raises(StoreUnavailable):
        await client.list_stores()


# ── the native Windows branch (addendum 18 audit) ────────────────────


async def test_native_windows_reads_appdata_from_the_env(monkeypatch, tmp_path):
    """Natively, %APPDATA% is in the environment and is used DIRECTLY —
    the PowerShell GetFolderPath fallback is WSL-only and must never
    run when the env is present (the audit's rule: no /mnt/c, no
    wslpath, no interop on the native path)."""
    cfg_dir = tmp_path / 'BitBrowser'
    cfg_dir.mkdir()
    (cfg_dir / 'config.json').write_text(
        json.dumps({'localServerAddress': 'http://127.0.0.1:54347'}),
        encoding='utf-8',
    )
    monkeypatch.setattr(bb_launch, 'get_platform', lambda: 'windows')
    monkeypatch.setenv('APPDATA', str(tmp_path))
    called = []

    def _no_powershell(script, timeout=30.0):
        called.append(script)
        raise AssertionError('PowerShell must not run on native Windows')

    monkeypatch.setattr(bb_launch, 'run_powershell', _no_powershell)
    client = bb.BitBrowserClient({})
    assert await client.discover_port() == 54347
    assert called == []


# ── C4: the hand-opened attach signal ────────────────────────────────


async def test_env_was_open_running_profile_is_true():
    routes = _default_routes()
    routes[('POST', '/browser/pids/all')] = (
        200,
        {'success': True, 'data': {'100000000001': 4242}},
    )
    server = await _serve(routes)
    client = _client(server.port)
    assert await client.env_was_open('100000000001') is True


async def test_env_was_open_closed_profile_is_false():
    routes = _default_routes()
    routes[('POST', '/browser/pids/all')] = (
        200,
        {'success': True, 'data': {'100000000002': 4242}},
    )
    server = await _serve(routes)
    client = _client(server.port)
    assert await client.env_was_open('100000000001') is False


@pytest.mark.parametrize(
    'payload',
    [
        {'success': False, 'msg': 'status unavailable'},
        {'success': True, 'data': None},
        'gateway error',
    ],
)
async def test_env_was_open_status_error_raises(payload):
    """Unknown status must never return None: a wrong guess could close
    a profile the user opened by hand."""
    routes = _default_routes()
    routes[('POST', '/browser/pids/all')] = (200, payload)
    server = await _serve(routes)
    client = _client(server.port)
    with pytest.raises(StoreUnavailable):
        await client.env_was_open('100000000001')


async def test_env_was_open_negative_control_has_no_none_fallback():
    """The three answers are True, False or an exception — never the
    base's unimplemented None."""
    routes = _default_routes()
    routes[('POST', '/browser/pids/all')] = (
        200,
        {'success': True, 'data': {}},
    )
    server = await _serve(routes)
    client = _client(server.port)
    answer = await client.env_was_open('100000000001')
    assert answer is False
    assert answer is not None


# ── C3: the vendor private-data declaration ──────────────────────────


def test_spec_declares_private_paths_and_local_ports():
    assert 'BitBrowser' in bb.BITBROWSER_VENDOR.private_paths
    assert 54345 in bb.BITBROWSER_VENDOR.local_ports


def test_guard_denies_the_declared_vendor_data_dir(monkeypatch):
    monkeypatch.setattr(
        vendor_guard,
        'registered_vendor_specs',
        lambda: {'bitbrowser': bb.BITBROWSER_VENDOR},
    )
    path = (
        '~/AppData/Roaming/BitBrowser/BrowserCache/100000000001/probe-state.dat'
    )
    reason = vendor_guard.vendor_guard_deny(
        'Read', {'file_path': path}, task_id='1a2b3c4d5e6f7a8b9c0d'
    )
    assert reason is not None and 'private data' in reason


def test_guard_negative_control_unrelated_dir_is_not_a_vendor_path(
    monkeypatch,
):
    monkeypatch.setattr(
        vendor_guard,
        'registered_vendor_specs',
        lambda: {'bitbrowser': bb.BITBROWSER_VENDOR},
    )
    path = '~/AppData/Roaming/OtherBrowser/probe-state.dat'
    assert (
        vendor_guard.vendor_guard_deny(
            'Read', {'file_path': path}, task_id='1a2b3c4d5e6f7a8b9c0d'
        )
        is None
    )


def test_guard_denies_the_declared_local_api_port(monkeypatch):
    monkeypatch.setattr(
        vendor_guard,
        'registered_vendor_specs',
        lambda: {'bitbrowser': bb.BITBROWSER_VENDOR},
    )
    reason = vendor_guard.vendor_guard_deny(
        'Bash',
        {'command': 'curl -s http://127.0.0.1:54345/browser/list'},
        task_id='1a2b3c4d5e6f7a8b9c0d',
    )
    assert reason is not None and '54345' in reason


# ── after_open: an about:blank-only browser is not ready ─────────────


async def test_after_open_navigates_a_blank_only_browser_to_the_store():
    cdp = FakeCDP(pages=[_page('about:blank', 'blank1')])
    await cdp.start()
    _servers.append(cdp)
    client = _client(1)
    client.after_open_delay_s = 0.05
    client.after_open_deadline_s = 0.2
    client._platform_url = 'https://www.amazon.com/'
    await client.after_open(OpenedStore('127.0.0.1', cdp.port))
    navigated = [p for m, p in cdp.commands if m == 'Page.navigate']
    assert [p.get('url') for p in navigated] == ['https://www.amazon.com/']
    assert _activated_ids(cdp) == ['blank1']


async def test_after_open_blank_negative_control_without_platform_url():
    """Without a known platform URL, no navigation is invented."""
    cdp = FakeCDP(pages=[_page('about:blank', 'blank1')])
    await cdp.start()
    _servers.append(cdp)
    client = _client(1)
    client.after_open_delay_s = 0.05
    client.after_open_deadline_s = 0.2
    client._platform_url = ''
    await client.after_open(OpenedStore('127.0.0.1', cdp.port))
    assert not [c for c in cdp.commands if c[0] == 'Page.navigate']
