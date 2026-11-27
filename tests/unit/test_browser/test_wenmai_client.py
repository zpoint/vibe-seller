"""RealWenmaiClient against a fake 稳卖 API server (the live shapes).

The HTTP layer, pagination, the two logged-out shapes, typed error
mapping, the account check, the middle-page click (over a real fake
CDP WebSocket target) and the launch/readiness flow — all on the
shapes verified live 2026-11-27 (client v5.1.0715). Placeholder data
only.
"""

import asyncio
from unittest import mock

from aiohttp import web
import pytest

from app.browser.adapters import wenmai_dom
import app.browser.adapters.wenmai_client as mod
from app.browser.adapters.wenmai_client import RealWenmaiClient
from app.browser.inject.client import OpenedStore
from app.browser.inject.errors import (
    AccountMismatch,
    CdpUnreachable,
    ClientNotRunning,
    LoginRequired,
    StoreNotFound,
    StoreUnavailable,
    UnsupportedKernel,
)
from tests.unit.test_browser.fake_wenmai_api import (
    FakeMiddlePageCdp,
    FakeWenmaiApi,
    _env,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    # The documented 0.5 s gap is real life, not CI.
    monkeypatch.setattr(RealWenmaiClient, 'min_interval_s', 0.0)


@pytest.fixture
async def api():
    server = FakeWenmaiApi()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


def _client(api: FakeWenmaiApi, cfg: dict | None = None) -> RealWenmaiClient:
    merged = {'api_port': api.port, **(cfg or {})}
    return RealWenmaiClient(merged)


# ── is_logged_out: the ONE function, both shapes ─────────────────────


def test_is_logged_out_relayed_shape():
    c = RealWenmaiClient({})
    assert c.is_logged_out(
        200,
        {
            'method': 'queryEnvV2',
            'msg': '认证失败，无法访问系统资源',
            'status': 'error',
            'code': '400',
        },
    )


def test_is_logged_out_user_info_shape():
    # getUserInfo answers code 200 with token null when logged out —
    # code 200 still means logged out there.
    c = RealWenmaiClient({})
    assert c.is_logged_out(
        200, {'response': {'code': 200, 'msg': '认证失败…', 'token': None}}
    )


def test_is_logged_out_negative_controls():
    c = RealWenmaiClient({})
    assert not c.is_logged_out(
        200, {'response': {'code': 200, 'msg': 'success', 'token': 'tok'}}
    )
    assert not c.is_logged_out(
        200, {'response': {'code': 200, 'total': 0, 'envs': []}}
    )
    assert not c.is_logged_out(200, None)
    assert not c.is_logged_out(200, {})


# ── list_stores ──────────────────────────────────────────────────────


async def test_list_stores_pages_until_total(api):
    api.stores = [_env(100000000000 + i, f'demo-{i}') for i in range(150)]
    stores = await _client(api).list_stores()
    assert len(stores) == 150
    assert api.call_log.count('queryEnvV2') == 2  # 100 + 50
    assert stores[0].ref == '100000000000'
    assert stores[149].name == 'demo-149'


async def test_list_stores_carries_device_bound_state(api):
    api.stores = [
        _env(100000000001, 'bound-store', ip_id=100200300),
        _env(100000000002, 'unbound-store', ip_id=None),
    ]
    stores = await _client(api).list_stores()
    by_name = {s.name: s for s in stores}
    assert by_name['bound-store'].available is True
    assert by_name['bound-store'].unavailable_reason == ''
    assert by_name['unbound-store'].available is False
    # The reason is a stable TOKEN the frontend translates; the
    # vendor's own words ride in the detail.
    assert by_name['unbound-store'].unavailable_reason == 'no_device'
    assert by_name['unbound-store'].unavailable_detail == '未绑定设备'


async def test_list_stores_logged_out_is_login_required(api):
    api.logged_out = True
    with pytest.raises(LoginRequired):
        await _client(api).list_stores()


async def test_list_stores_negative_control_success(api):
    # Twist: the same server, logged in → a real list, never an error.
    api.logged_out = False
    stores = await _client(api).list_stores()
    assert [s.name for s in stores] == ['demo-store']


# ── open_store ───────────────────────────────────────────────────────


async def test_open_store_parses_the_cdp_endpoint(api):
    cdp = FakeMiddlePageCdp()
    await cdp.start()
    try:
        api.cdp_port = cdp.port
        opened = await _client(api).open_store('100000000001')
        assert opened.cdp_host == '127.0.0.1'
        assert opened.cdp_port == cdp.port
    finally:
        await cdp.stop()


async def test_open_store_reopen_returns_the_same_port(api):
    # Opening an already-open store returns the SAME pid and port —
    # a store opened by hand attaches without reopening.
    cdp = FakeMiddlePageCdp()
    await cdp.start()
    try:
        api.cdp_port = cdp.port
        client = _client(api)
        first = await client.open_store('100000000001')
        second = await client.open_store('100000000001')
        assert second.cdp_port == first.cdp_port
    finally:
        await cdp.stop()


async def test_open_store_device_unbound_is_store_unavailable(api):
    api.open_results[100000000001] = 'unbound'
    with pytest.raises(StoreUnavailable) as ei:
        await _client(api).open_store('100000000001')
    # Carries the vendor's own message plus the product fix hint.
    assert '未绑定' in str(ei.value)
    assert '稳卖' in str(ei.value)


async def test_open_store_unknown_id_is_store_not_found(api):
    with pytest.raises(StoreNotFound):
        await _client(api).open_store('999999999999')


async def test_open_store_firefox_kernel_is_unsupported(api):
    # A Firefox env answers with webdriverPath null and no debug port —
    # covered by a fake because no Firefox store exists to verify live.
    api.open_results[100000000001] = 'firefox'
    api.stores = [_env(100000000001, 'demo-store')]
    # The fake answers the firefox refusal before webdriverPath is seen;
    # exercise parse_opened's guard directly for the null-webdriverPath
    # shape, plus the end-to-end mapping for the refusal.
    with pytest.raises((UnsupportedKernel, CdpUnreachable)):
        await _client(api).open_store('100000000001')
    client = RealWenmaiClient({})
    with pytest.raises(UnsupportedKernel):
        client.parse_opened({
            'code': 200,
            'msg': 'success',
            'pid': 1,
            'selenium': '',
            'webdriverPath': None,
        })


async def test_open_store_logged_out_is_login_required(api):
    api.logged_out = True
    with pytest.raises(LoginRequired):
        await _client(api).open_store('100000000001')


# ── map_error: the ONE mapping function ──────────────────────────────


def test_map_error_covers_the_vendor_shapes():
    """The ONE mapping function — for the shapes the shared response
    path does NOT already handle (logged-out is is_logged_out's job,
    checked before map_error in api_call)."""
    c = RealWenmaiClient({})
    err = c.map_error(
        200,
        {
            'status': 'error',
            'code': '400',
            'msg': '当前店铺未绑定可用设备，无法启动',
        },
    )
    assert isinstance(err, StoreUnavailable)
    assert '稳卖' in str(err)  # the product fix hint lives HERE
    assert isinstance(
        c.map_error(200, {'status': 'error', 'code': '400', 'msg': 'boom'}),
        CdpUnreachable,
    )
    assert c.map_error(200, {'response': {'code': 200}}) is None
    assert c.map_error(200, None) is None


# ── open_store: a dead endpoint is closed and re-opened ──────────────


async def test_open_store_recovers_a_dead_endpoint(api, monkeypatch):
    """A kernel the client still lists as open whose endpoint never
    answers (seen live: openEnv kept returning a port nothing listened
    on) is closed through the client and re-opened fresh."""
    cdp = FakeMiddlePageCdp()
    await cdp.start()
    try:
        api.cdp_port = cdp.port + 1  # dead: nothing listens there
        client = _client(api)
        flips = {'n': 0}

        async def fake_sleep(_s):
            flips['n'] += 1
            if flips['n'] >= 1:  # the re-open lands on the live port
                api.cdp_port = cdp.port

        monkeypatch.setattr(mod.asyncio, 'sleep', fake_sleep)
        # (the real probe is used; a refused port fails fast)
        opened = await client.open_store('100000000001')
        assert opened.cdp_port == cdp.port
        assert 'closeEnv' in api.call_log  # the dead env was closed
    finally:
        await cdp.stop()


async def test_open_store_dead_endpoint_twice_is_cdp_unreachable(
    api, monkeypatch
):
    """Negative control: the re-opened endpoint is STILL dead after a
    confirmed kernel kill → a clear CdpUnreachable naming the
    recovery, not a hang or a wrong error."""
    cdp = FakeMiddlePageCdp()
    await cdp.start()
    try:
        api.cdp_port = cdp.port + 1  # dead now and after the recovery
        client = _client(api)
        monkeypatch.setattr(
            client, '_endpoint_answers', mock.AsyncMock(return_value=False)
        )
        kills = []

        def fake_kill(port, env_id):
            kills.append((port, env_id))
            return True  # the stuck kernel WAS identified and killed

        monkeypatch.setattr(
            client._proc, 'kill_kernel_by_debug_port', fake_kill
        )
        with pytest.raises(CdpUnreachable) as ei:
            await client.open_store('100000000001')
        assert 'never answered' in str(ei.value)
        assert 'closeEnv' in api.call_log
        assert kills == [(cdp.port + 1, '100000000001')]
    finally:
        await cdp.stop()


async def test_open_store_unidentified_kernel_is_a_safe_refusal(
    api, monkeypatch
):
    """W2: when the stuck kernel cannot be CONFIRMED as this store's
    (another app may hold the port), kill nothing and fail with the
    close-it-in-the-client hint — never guess at a kill."""
    cdp = FakeMiddlePageCdp()
    await cdp.start()
    try:
        api.cdp_port = cdp.port + 1
        client = _client(api)
        monkeypatch.setattr(
            client, '_endpoint_answers', mock.AsyncMock(return_value=False)
        )
        kills = []

        def no_match_kill(port, env_id):
            kills.append((port, env_id))
            return False  # the three-check match found nothing

        monkeypatch.setattr(
            client._proc, 'kill_kernel_by_debug_port', no_match_kill
        )
        with pytest.raises(CdpUnreachable) as ei:
            await client.open_store('100000000001')
        assert 'could not be identified for a safe kill' in str(ei.value)
        assert '稳卖 client' in str(ei.value)  # the operator hint
        assert len(kills) == 1  # the check ran once; nothing was killed
    finally:
        await cdp.stop()


# ── after_open: the middle page ──────────────────────────────────────


async def test_after_open_clicks_open_store_on_the_middle_page():
    cdp = FakeMiddlePageCdp()
    await cdp.start()
    try:
        client = RealWenmaiClient({})
        await client.after_open(OpenedStore('127.0.0.1', cdp.port))
        assert cdp.evals, 'the 打开店铺 button was never clicked'
        assert cdp.store_page_open is True
    finally:
        await cdp.stop()


async def test_after_open_waits_for_the_page_to_be_ready():
    """The middle page renders async: openEnv answers when the KERNEL
    is up, but the button (and its handler) appears a moment later. A
    click fired before that is silently dropped — after_open must wait
    for readiness and click again, not lose the race (seen live)."""
    cdp = FakeMiddlePageCdp()
    await cdp.start()
    try:
        cdp.ready = False  # the page has not rendered its button yet
        client = RealWenmaiClient({})
        # Flip to ready after the first readiness probe.
        real_eval = client._cdp_eval

        async def flipping_eval(target, js):
            if js == wenmai_dom.MIDDLE_READY_JS and not cdp.ready:
                cdp.ready = True
            return await real_eval(target, js)

        client._cdp_eval = flipping_eval
        await client.after_open(OpenedStore('127.0.0.1', cdp.port))
        assert cdp.store_page_open is True
        # Every click on the fake is recorded; the lost-early-click
        # case would leave store_page_open False (the fake drops
        # clicks made before ready).
    finally:
        await cdp.stop()


async def test_after_open_skips_when_the_store_page_is_already_there():
    # 以后自动打开, or a person opened the store: the platform page
    # already exists → no click.
    cdp = FakeMiddlePageCdp()
    await cdp.start()
    try:
        cdp.store_page_open = True
        client = RealWenmaiClient({})
        await client.after_open(OpenedStore('127.0.0.1', cdp.port))
        assert cdp.evals == []
    finally:
        await cdp.stop()


async def test_after_open_negative_control_no_pages_never_raises():
    # Twist: no middlePage target and no store page → warn + return
    # (the env IS open; the agent can navigate), never a raise.
    app = web.Application()

    async def empty(_r):
        return web.json_response([])

    app.router.add_get('/json/list', empty)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    empty_port = runner.addresses[0][1]
    try:
        client = RealWenmaiClient({})
        await client.after_open(OpenedStore('127.0.0.1', empty_port))
    finally:
        await runner.cleanup()


# ── ensure_running: launch + relay readiness ─────────────────────────


class _FakeProc:
    def __init__(self):
        self.launched = False
        self.locate_error = None

    def launch(self):
        self.launched = True
        return True

    def main_pid(self):
        return 4242

    def exe_path_script(self):
        return "$exe='C:\\demo\\app.exe';"


async def test_ensure_running_launches_when_the_api_is_dead(monkeypatch):
    proc = _FakeProc()
    monkeypatch.setattr(mod, 'WenmaiProcessLayer', lambda _p=None: proc)
    api = FakeWenmaiApi()
    await api.start()
    try:
        # The client's port points at a DEAD port first: only after
        # launch does the fake API answer (simulating the launch).
        # NB: launch runs via asyncio.to_thread, so it must be SYNC —
        # an async fake would return an un-awaited coroutine and the
        # readiness loop would spin on the dead port for its 120 s
        # budget (this exact hang was why the suite stalled).
        client = RealWenmaiClient({'api_port': api.port + 1})

        def fake_launch():
            proc.launched = True
            client._port = api.port
            return True

        proc.launch = fake_launch
        await client.ensure_running()
        assert proc.launched
    finally:
        await api.stop()


async def test_ensure_running_raises_when_launch_fails(monkeypatch):
    proc = _FakeProc()
    proc.launch = lambda: False
    proc.locate_error = 'not found anywhere'
    monkeypatch.setattr(mod, 'WenmaiProcessLayer', lambda _p=None: proc)
    # A port nothing listens on: the connect timeout bounds the hang
    # (an unbound loopback port on WSL hangs instead of refusing).
    monkeypatch.setattr(RealWenmaiClient, 'connect_timeout_s', 0.2)
    client = RealWenmaiClient({'api_port': 1})  # nothing listens
    with pytest.raises(ClientNotRunning):
        await client.ensure_running()


async def test_ensure_running_waits_for_a_slow_relay(api, monkeypatch):
    """The relay answers only after the main window loads — readiness
    is the RELAY answering, not the shell. First poll times out (the
    relay hangs past the call budget), the retry loop succeeds once
    the relay is up; ensure_running must ride through both."""
    api.slow_relay_s = 0.4  # longer than the client's call budget
    client = _client(api)
    monkeypatch.setattr(client, 'request_timeout_s', 0.05)
    polls = {'n': 0}

    async def fast_sleep(_s):
        polls['n'] += 1
        if polls['n'] >= 2:  # after the first timed-out poll: relay up
            api.slow_relay_s = 0.0

    with mock.patch.object(mod.asyncio, 'sleep', new=fast_sleep):
        await client.ensure_running()
    assert 'queryEnvV2' in api.call_log
    assert polls['n'] >= 1  # it polled, it did not assume ready


async def test_ensure_running_negative_control_dead_relay_raises(
    api, monkeypatch
):
    """Twist: the relay NEVER answers (window never loads) → a clear
    ClientNotRunning after the bounded budget, not an infinite wait."""
    api.slow_relay_s = 5.0
    client = _client(api)
    monkeypatch.setattr(client, 'request_timeout_s', 0.05)
    monkeypatch.setattr(client, 'relay_timeout_s', 0.3)
    with pytest.raises(ClientNotRunning):
        await client.ensure_running()


# ── login state + account check ──────────────────────────────────────


async def test_is_logged_in_reads_the_token(api):
    assert await _client(api).is_logged_in() is True
    api.logged_out = True
    assert await _client(api).is_logged_in() is False


async def test_ensure_logged_in_noop_when_logged_in(api):
    client = _client(
        api,
        {
            'wenmai_company': 'acme',
            'wenmai_username': 'u',
            'wenmai_password': 'p',
        },
    )
    await client.ensure_logged_in()  # no inspector touched


async def test_ensure_logged_in_account_mismatch(api):
    # Logged in as a DIFFERENT company than the saved one → a clear
    # AccountMismatch; never an automatic logout.
    api.company = 'other-company'
    client = _client(
        api,
        {
            'wenmai_company': 'acme',
            'wenmai_username': 'u',
            'wenmai_password': 'p',
        },
    )
    with pytest.raises(AccountMismatch) as ei:
        await client.ensure_logged_in()
    assert 'other-company' in str(ei.value)


class _FakeInspector:
    """Renderer evals for the login flow: submitting the enterprise
    form flips the fake API's login state (the real client's token
    appears in getUserInfo once the login takes)."""

    def __init__(self, api, *, logs_in=True, form_result='OK'):
        self._api = api
        self._logs_in = logs_in
        self._form_result = form_result
        self.submitted = False
        self.opened = False

    async def open(self, pid):
        self.opened = True

    async def close(self):
        pass

    async def eval_in_renderer(self, js, **kw):
        if '企业名称' in js:  # the fill_enterprise_login_js payload
            # 'submitted' = the form's 登录 button was clicked, which
            # only happens when the fields were found and filled.
            if self._form_result == 'OK':
                self.submitted = True
                if self._logs_in:
                    self._api.logged_out = False
            return self._form_result
        return None


def _patch_login(monkeypatch, insp):
    monkeypatch.setattr(mod, 'WenmaiProcessLayer', lambda _p=None: _FakeProc())
    monkeypatch.setattr(mod, 'InspectorSession', lambda _s: insp)


async def test_ensure_logged_in_auto_logs_in_via_the_inspector(
    api, monkeypatch
):
    api.logged_out = True
    insp = _FakeInspector(api)
    _patch_login(monkeypatch, insp)
    client = _client(
        api,
        {
            'wenmai_company': 'acme',
            'wenmai_username': 'u',
            'wenmai_password': 'p',
        },
    )
    await client.ensure_logged_in()
    assert insp.submitted and insp.opened
    # The poll confirmed the LOGIN (the API token), not a UI hash.
    assert await client.is_logged_in() is True


async def test_credentials_arrive_under_the_registry_key_names(
    api, monkeypatch
):
    """W1: the registry injects machine credentials as
    ``wenmai_<field>`` (vendor_creds.inject_vendor_config) — the client
    must read THOSE keys, or auto-login would find no credentials once
    the spec registers. The spec's own field keys stay
    company/username/password (declared in wenmai.py)."""
    api.logged_out = True
    insp = _FakeInspector(api)
    _patch_login(monkeypatch, insp)
    client = _client(
        api,
        {
            'wenmai_company': 'acme',
            'wenmai_username': 'u',
            'wenmai_password': 'p',
        },
    )
    await client.ensure_logged_in()
    assert insp.submitted  # the credentials were found and driven


async def test_neutral_credential_keys_are_not_read(api, monkeypatch):
    """Negative control: a config carrying ONLY the neutral field names
    (pre-registry shape) has no credentials as far as the client is
    concerned — LoginRequired, not a silent half-login."""
    api.logged_out = True
    insp = _FakeInspector(api)
    _patch_login(monkeypatch, insp)
    client = _client(api, {'company': 'acme', 'username': 'u', 'password': 'p'})
    with pytest.raises(LoginRequired):
        await client.ensure_logged_in()
    assert insp.submitted is False


async def test_ensure_logged_in_no_creds_raises(api):
    api.logged_out = True
    with pytest.raises(LoginRequired):
        await _client(api).ensure_logged_in()


async def test_ensure_logged_in_form_failure_names_what_was_missing(
    api, monkeypatch
):
    api.logged_out = True
    insp = _FakeInspector(api, form_result='NOFIELDS')
    _patch_login(monkeypatch, insp)
    client = _client(
        api,
        {
            'wenmai_company': 'acme',
            'wenmai_username': 'u',
            'wenmai_password': 'p',
        },
    )
    with pytest.raises(LoginRequired) as ei:
        await client.ensure_logged_in()
    assert 'NOFIELDS' in str(ei.value)  # pinpointed, not a generic raise
    assert insp.submitted is False  # never reached the submit


async def test_ensure_logged_in_submitted_but_stays_logged_out_raises(
    api, monkeypatch
):
    """A submitted form that never produces a token (wrong password)
    raises after the bounded poll — the poll watches the API, not the
    flaky window hash (a successful login reloads the window and tore
    the inspector's target down; seen live)."""
    api.logged_out = True
    insp = _FakeInspector(api, logs_in=False)
    _patch_login(monkeypatch, insp)
    client = _client(
        api,
        {
            'wenmai_company': 'acme',
            'wenmai_username': 'u',
            'wenmai_password': 'p',
        },
    )
    with pytest.raises(LoginRequired) as ei:
        await client.ensure_logged_in()
    assert 'stayed logged out' in str(ei.value)
    assert insp.submitted  # the form WAS driven; the login did not take


# ── serialization ────────────────────────────────────────────────────


async def test_calls_are_serialised_one_at_a_time(api):
    """Two concurrent calls never overlap: the per-loop gate holds one
    request at a time (the documented one-request-per-0.5s limit).
    Measured SERVER-side, inside the fake API's handlers — wrapping the
    client method would count queueing, not the gate."""
    client = _client(api)
    await asyncio.gather(client.is_logged_in(), client.list_stores())
    assert api.peak == 1
    assert len(api.call_log) >= 2  # both callers finished their calls
