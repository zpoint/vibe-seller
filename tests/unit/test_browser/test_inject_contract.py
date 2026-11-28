"""The inject-engine vendor contract: StoreRef, after_open, error codes.

These pin the pieces PR "inject contract" adds so later vendor adapters
can rely on them: the engine calls ``after_open`` exactly once, after
CDP is verified and before the proxy fronts the env, and never when the
open failed; ``StoreRef``'s stable-ref fields default safely; every new
error class carries its taxonomy code and a vendor-neutral hint.
"""

import asyncio
from http.server import (
    BaseHTTPRequestHandler,
    ThreadingHTTPServer,
)
import json as _json
from pathlib import Path
import threading
import types
from unittest import mock

import aiohttp
import pytest

from app.browser.adapters.yuehulian_win import (
    InspectorSession as YhlInspector,
    ProcessLayer,
)
from app.browser.inject import engine as engine_mod, errors as errors_mod
from app.browser.inject.client import (
    UNAVAILABLE_NO_DEVICE,
    UNAVAILABLE_OTHER,
    UNAVAILABLE_TOKENS,
    InjectClient,
    OpenedStore,
    StoreRef,
)
from app.browser.inject.engine import InjectCdpBackend
from app.browser.inject.errors import (
    AccountMismatch,
    ApiNotEnabled,
    InjectError,
    PlanRequired,
    StoreNotFound,
    StoreUnavailable,
    UnsupportedKernel,
)
from app.browser.machine_lock import MachineLock
import app.browser.win_inspector as win_inspector_mod
from app.browser.win_inspector import (
    InspectorError,
    InspectorSession as WinInspectorSession,
)

pytestmark = pytest.mark.unit


# ── a recording fake client (vendor-neutral) ─────────────────────────


class _RecordingClient(InjectClient):
    """Records the order the engine drives it, with optional failures."""

    def __init__(self, *, fail_open=None, fail_cdp=False, cdp_port=0):
        self.events: list[str] = []
        self._fail_open = fail_open
        self._fail_cdp = fail_cdp
        self._cdp_port = cdp_port
        self.after_open_calls = 0

    async def ensure_running(self) -> None:
        self.events.append('ensure_running')

    async def is_logged_in(self) -> bool:
        return True

    async def list_stores(self) -> list[StoreRef]:
        return [StoreRef('store-a')]

    async def open_store(self, ref: str) -> OpenedStore:
        self.events.append('open')
        if self._fail_open is not None:
            raise self._fail_open
        return OpenedStore('127.0.0.1', self._cdp_port)

    async def after_open(self, opened: OpenedStore) -> None:
        self.after_open_calls += 1
        self.events.append('after_open')

    async def close_store(self, ref: str) -> None:
        self.events.append('close')


def _backend(client: _RecordingClient) -> InjectCdpBackend:
    return InjectCdpBackend(client_factory=lambda cfg: client)


def _cfg() -> dict:
    return {'browser_ref': 'store-a', 'proxy_port': 9299, 'store_slug': 's'}


@pytest.fixture
def _mocked_proxy():
    with mock.patch.object(engine_mod, 'CDPMuxProxy') as proxy:
        proxy.return_value.start = mock.AsyncMock()
        proxy.return_value.stop = mock.AsyncMock()
        yield proxy


# ── after_open: called once, after CDP verify, before the proxy ──────


async def test_after_open_runs_once_after_cdp_and_before_proxy(
    cdp_version_port, tmp_path, _mocked_proxy
):
    """Happy path ordering: open → CDP verified → after_open → proxy."""
    client = _RecordingClient(cdp_port=cdp_version_port)
    backend = _backend(client)
    cdp_probe = mock.AsyncMock(return_value={'Browser': 'Chrome/135.0.0.0'})
    with (
        mock.patch.object(InjectCdpBackend, '_cdp_version', new=cdp_probe),
        mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path),
    ):
        await backend.start(_cfg())
    # after_open ran exactly once, and the CDP probe had already
    # answered when it did (open < cdp < after_open < proxy.start).
    assert client.after_open_calls == 1
    assert client.events == ['ensure_running', 'open', 'after_open']
    assert cdp_probe.await_count >= 1
    _mocked_proxy.return_value.start.assert_awaited_once()


async def test_after_open_not_called_when_open_fails(
    cdp_version_port, tmp_path, _mocked_proxy
):
    """Negative control: a failed open never drives the entry page."""
    client = _RecordingClient(
        fail_open=StoreNotFound('store-a'), cdp_port=cdp_version_port
    )
    with (
        pytest.raises(StoreNotFound),
        mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path),
    ):
        await _backend(client).start(_cfg())
    assert client.after_open_calls == 0
    _mocked_proxy.return_value.start.assert_not_called()


async def test_after_open_not_called_when_cdp_never_answers(
    cdp_version_port, tmp_path, _mocked_proxy
):
    """Negative control: CDP dead ⇒ no entry-page driving, no proxy.

    Clicking an intermediate page on an env whose debug endpoint never
    came up would produce a task that "opened" a store it cannot drive.
    """
    client = _RecordingClient(cdp_port=cdp_version_port)
    with (
        pytest.raises(errors_mod.CdpUnreachable),
        mock.patch.object(
            InjectCdpBackend,
            '_cdp_version',
            new=mock.AsyncMock(return_value=None),
        ),
        mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path),
    ):
        await _backend(client).start(_cfg())
    assert client.after_open_calls == 0
    _mocked_proxy.return_value.start.assert_not_called()


# ── a failed start closes the env it opened ──────────────────────────


async def test_cdp_never_answers_closes_the_env_it_opened(
    cdp_version_port, tmp_path, _mocked_proxy
):
    """The bug this pins (seen live: a Firefox/BiDi env survived with 11
    processes): an open that succeeds but whose CDP never answers raised
    CdpUnreachable WITHOUT closing — the store window outlived the task.
    Every post-open failure must close the env the open created."""
    client = _RecordingClient(cdp_port=cdp_version_port)
    with (
        pytest.raises(errors_mod.CdpUnreachable),
        mock.patch.object(
            InjectCdpBackend,
            '_cdp_version',
            new=mock.AsyncMock(return_value=None),
        ),
        mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path),
    ):
        await _backend(client).start(_cfg())
    # The env the open created was closed again: open → close, and
    # nothing else (no entry page, no proxy).
    assert client.events == ['ensure_running', 'open', 'close']
    _mocked_proxy.return_value.start.assert_not_called()


class _FailingCloseClient(_RecordingClient):
    """A vendor whose close call itself fails (client already gone)."""

    async def close_store(self, ref: str) -> None:
        self.events.append('close')
        raise RuntimeError('vendor close failed')


async def test_cdp_unreachable_is_never_masked_by_a_failed_close(
    cdp_version_port, tmp_path, _mocked_proxy
):
    """The cleanup is best-effort: a close_store that itself fails is
    logged and swallowed — the caller still sees the ORIGINAL
    CdpUnreachable, never the cleanup error."""
    client = _FailingCloseClient(cdp_port=cdp_version_port)
    with (
        pytest.raises(errors_mod.CdpUnreachable),
        mock.patch.object(
            InjectCdpBackend,
            '_cdp_version',
            new=mock.AsyncMock(return_value=None),
        ),
        mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path),
    ):
        await _backend(client).start(_cfg())
    # The close was ATTEMPTED (and its failure swallowed): the original
    # error is what surfaced above.
    assert client.events == ['ensure_running', 'open', 'close']


class _FailingAfterOpenClient(_RecordingClient):
    """A vendor whose entry step dies (the intermediate page changed)."""

    async def after_open(self, opened: OpenedStore) -> None:
        self.events.append('after_open')
        raise RuntimeError('entry page click failed')


async def test_after_open_failure_closes_the_env(
    cdp_version_port, tmp_path, _mocked_proxy
):
    """The cleanup is one funnel, not a per-branch patch: ANY post-open
    failure — here the vendor's entry step — closes the env and
    re-raises the original error."""
    client = _FailingAfterOpenClient(cdp_port=cdp_version_port)
    cdp_probe = mock.AsyncMock(return_value={'Browser': 'Chrome/135.0.0.0'})
    with (
        pytest.raises(RuntimeError, match='entry page click failed'),
        mock.patch.object(InjectCdpBackend, '_cdp_version', new=cdp_probe),
        mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path),
    ):
        await _backend(client).start(_cfg())
    assert client.events == [
        'ensure_running',
        'open',
        'after_open',
        'close',
    ]
    _mocked_proxy.return_value.start.assert_not_called()


class _HangingAfterOpenClient(_RecordingClient):
    """A vendor entry step that never finishes on its own (slow page)."""

    def __init__(self, cdp_port: int):
        super().__init__(cdp_port=cdp_port)
        self.entered = asyncio.Event()

    async def after_open(self, opened: OpenedStore) -> None:
        self.events.append('after_open')
        self.entered.set()
        await asyncio.Event().wait()  # never completes


async def test_a_cancelled_start_still_closes_the_env(
    cdp_version_port, tmp_path, _mocked_proxy
):
    """Cancellation is a post-open failure like any other (review R1-1):
    a task stop or a server shutdown lands mid-entry-step — the env the
    open created is closed, and the CancelledError still propagates.
    The cleanup stays best-effort: a second cancel during it would
    simply end it."""
    client = _HangingAfterOpenClient(cdp_version_port)
    backend = _backend(client)
    cdp_probe = mock.AsyncMock(return_value={'Browser': 'Chrome/135.0.0.0'})
    with (
        mock.patch.object(InjectCdpBackend, '_cdp_version', new=cdp_probe),
        mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path),
    ):
        task = asyncio.create_task(backend.start(_cfg()))
        await client.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert client.events == [
        'ensure_running',
        'open',
        'after_open',
        'close',
    ]
    _mocked_proxy.return_value.start.assert_not_called()


async def test_proxy_start_failure_stops_the_half_started_proxy(
    cdp_version_port, tmp_path, _mocked_proxy
):
    """The proxy arm of the funnel (review R1-2): a proxy that fails to
    start (its port taken) is stopped, the env is closed, and the
    ORIGINAL error propagates."""
    client = _RecordingClient(cdp_port=cdp_version_port)
    _mocked_proxy.return_value.start = mock.AsyncMock(
        side_effect=OSError('proxy port taken')
    )
    cdp_probe = mock.AsyncMock(return_value={'Browser': 'Chrome/135.0.0.0'})
    with (
        pytest.raises(OSError, match='proxy port taken'),
        mock.patch.object(InjectCdpBackend, '_cdp_version', new=cdp_probe),
        mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path),
    ):
        await _backend(client).start(_cfg())
    _mocked_proxy.return_value.stop.assert_awaited_once()
    assert client.events == ['ensure_running', 'open', 'after_open', 'close']


async def test_after_open_default_is_a_no_op(
    cdp_version_port, tmp_path, _mocked_proxy
):
    """A client that does not override after_open starts unchanged."""
    client = _RecordingClient(cdp_port=cdp_version_port)
    # Simulate "no override": drop to the base implementation.
    client.after_open = InjectClient.after_open.__get__(client)
    with mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path):
        info = await _backend(client).start(_cfg())
    assert info.cdp_port == cdp_version_port


# ── StoreRef: stable ref + availability, defaulting safely ───────────


def test_store_ref_defaults_to_name_as_ref():
    ref = StoreRef('store-a')
    assert ref.ref == ''
    assert ref.available is True
    assert ref.open_ref == 'store-a'  # name is the ref when unset


def test_store_ref_explicit_ref_and_unavailable():
    # Negative control: an explicit ref wins over the name, and an
    # unavailable store carries its reason TOKEN (vendor-neutral; the
    # frontend translates browserVendor.unavailable.<token>) plus
    # optional free-text detail for a tooltip.
    ref = StoreRef(
        'demo-store',
        ref='100000000001',
        available=False,
        unavailable_reason=UNAVAILABLE_NO_DEVICE,
        unavailable_detail='vendor said: no device bound',
    )
    assert ref.open_ref == '100000000001'
    assert ref.available is False
    assert ref.unavailable_reason == 'no_device'
    assert ref.unavailable_detail == 'vendor said: no device bound'
    assert ref.name == 'demo-store'


def test_unavailable_reason_is_a_closed_vocabulary():
    """The tokens are the contract the frontend translates
    (browserVendor.unavailable.<token>): every member is a short
    snake_case slug, and a vendor reason outside the vocabulary maps
    to `other` + detail — never to prose."""
    assert UNAVAILABLE_TOKENS == frozenset({
        'no_device',
        'unsupported_kernel',
        'expired',
        'disabled',
        'other',
    })
    for token in UNAVAILABLE_TOKENS:
        assert token == token.lower()
        assert ' ' not in token
    assert UNAVAILABLE_OTHER in UNAVAILABLE_TOKENS


def test_store_ref_defaults_empty_reason_and_detail():
    ref = StoreRef('store-a')
    assert ref.unavailable_reason == ''
    assert ref.unavailable_detail == ''


# ── error taxonomy: code + hint formatting, vendor-neutral ──────────


@pytest.mark.parametrize(
    'cls,code',
    [
        (AccountMismatch, 'ACCOUNT_MISMATCH'),
        (ApiNotEnabled, 'API_NOT_ENABLED'),
        (PlanRequired, 'PLAN_REQUIRED'),
        (StoreUnavailable, 'STORE_UNAVAILABLE'),
    ],
)
def test_new_errors_carry_their_code_and_hint(cls, code):
    e = cls()
    assert e.code == code
    assert e.hint  # never an empty hint: it is the default message
    assert str(e) == e.hint  # no message → the hint IS the message


def test_unsupported_kernel_default_text_is_code_plus_hint():
    # UnsupportedKernel is the one error whose text carries its code
    # token (the UI keys the docs link on it) — pinned separately.
    e = UnsupportedKernel()
    assert str(e) == '[' + e.code + '] ' + e.hint


def test_unsupported_kernel_text_carries_the_stable_code():
    """The task-error text (what the UI links docs by) carries the
    taxonomy code WHATEVER message a vendor passes — prose is
    translated and vendor-specific, the token is neither."""
    assert 'UNSUPPORTED_KERNEL' in str(UnsupportedKernel())
    assert 'UNSUPPORTED_KERNEL' in str(
        UnsupportedKernel('vendor said: firefox kernel, no debug port')
    )
    # And the token is prefix-bracketed so a match cannot hit prose.
    assert str(UnsupportedKernel()).startswith('[UNSUPPORTED_KERNEL] ')


def test_other_errors_do_not_gain_the_code_token():
    """Negative control (R5): only UnsupportedKernel carries the token;
    the pre-existing errors read exactly as before."""
    for err in (
        errors_mod.ClientNotRunning(),
        errors_mod.LoginRequired(),
        errors_mod.StoreNotFound(),
        errors_mod.CdpUnreachable(),
    ):
        text = str(err)
        assert not text.startswith('[')
        assert err.code not in text


def test_store_unavailable_carries_the_vendor_message():
    # The one error meant to hold vendor text: the constructor message
    # wins over the generic hint.
    e = StoreUnavailable('vendor said: no device bound')
    assert str(e) == 'vendor said: no device bound'
    assert e.code == 'STORE_UNAVAILABLE'


def test_error_codes_do_not_collide():
    # Negative control: every taxonomy code is distinct, so a status
    # keyed by code names exactly one failure.
    codes = [
        c.code
        for c in (
            errors_mod.ClientNotRunning,
            errors_mod.LoginRequired,
            AccountMismatch,
            ApiNotEnabled,
            PlanRequired,
            errors_mod.StoreNotFound,
            StoreUnavailable,
            UnsupportedKernel,
            errors_mod.CdpUnreachable,
            errors_mod.AccountConflict,
            errors_mod.IdleExpired,
            errors_mod.UnsupportedPlatform,
        )
    ]
    assert len(codes) == len(set(codes))


def test_errors_module_names_no_vendor():
    """Vendor-neutral by construction: no product name may appear in
    the shared taxonomy (product text lives in vendor modules/specs)."""
    src = Path(errors_mod.__file__).read_text(encoding='utf-8').lower()
    for vendor in ('wenmai', '稳卖', 'yuehulian', '悦互联', 'ziniao', '紫鸟'):
        assert vendor not in src, vendor


def test_inject_error_message_falls_back_to_code():
    # A subclass without a hint still stringifies to its code.
    class _Bare(InjectError):
        code = 'BARE'

    assert str(_Bare()) == 'BARE'


# ── inspector extraction ─────────────────────────────────────────────


def test_yuehulian_inspector_is_the_shared_one():
    """The yuehulian subclass delegates to app.browser.win_inspector."""
    assert issubclass(YhlInspector, WinInspectorSession)
    # Constructed with a ProcessLayer, it uses THAT proc's exe script.
    proc = ProcessLayer(r'D:\custom dir')
    insp = YhlInspector(proc)
    script = insp._exe_path_script()
    assert script.startswith('$exe=')
    assert 'D:\\custom dir' in script


def _not_busy_then_serving(url='ws://127.0.0.1:9229/devtools/browser/x'):
    """A discovery fake: busy-check sees nothing, then the target."""
    state = {'serving': False}

    async def fake_discover(self):
        if not state['serving']:
            state['serving'] = True
            return None  # the pre-open busy check: nothing serving
        return url

    return fake_discover


async def _no_connect(self, ws_url, expr, await_promise, timeout):
    """Stand-in for _evaluate_once: fail INSTANTLY. The real one, on a
    dead endpoint, can take its full bound in seconds (an unbound
    loopback port hangs instead of refusing on some hosts), which no
    unit test should pay for."""
    raise win_inspector_mod.InspectorError('test: no real connect')


def _fast_busy(monkeypatch, tmp_path):
    """Machine lock in tmp + zero busy backoff + instant-eval: the
    busy-retry tests run in milliseconds and count attempts, never
    wall-clock seconds."""
    _fake_lock(monkeypatch, tmp_path)
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, 'BUSY_BACKOFF_S', 0.0
    )
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_evaluate_once', _no_connect
    )


def _fake_lock(monkeypatch, tmp_path):
    """Point the session's machine lock at the test's tmp dir."""
    monkeypatch.setattr(
        win_inspector_mod,
        'MachineLock',
        lambda name: MachineLock(name, tmp_path),
    )


def test_shared_inspector_uses_any_exe_script_callable(monkeypatch, tmp_path):
    """The shared session needs no client knowledge beyond the callable
    that resolves ``$exe`` — any vendor can supply it."""
    scripts = []

    def fake_run(script, *a, **kw):
        scripts.append(script)
        return 'ok'

    async def fake_pid(self, ws_url=None):
        return 4242

    _fake_lock(monkeypatch, tmp_path)
    monkeypatch.setattr(win_inspector_mod, '_run_inspector_ps', fake_run)
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession,
        '_discover_ws',
        _not_busy_then_serving(),
    )
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_probe_pid', fake_pid
    )
    insp = WinInspectorSession(lambda: "$exe='C:\\x\\app.exe';")
    asyncio.run(insp.open(4242))
    assert len(scripts) == 1
    assert "$exe='C:\\x\\app.exe';" in scripts[0]
    assert 'process._debugProcess(4242)' in scripts[0]
    assert "$env:ELECTRON_RUN_AS_NODE='1';" in scripts[0]


def test_shared_inspector_raises_when_discovery_fails(monkeypatch, tmp_path):
    """Negative control: no inspector websocket ⇒ InspectorError, not a
    silent session."""
    monkeypatch.setattr(
        win_inspector_mod, '_run_inspector_ps', lambda *a, **kw: 'ok'
    )

    async def no_ws(self):
        return None

    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_discover_ws', no_ws
    )

    async def fast_sleep(_s):
        return None

    monkeypatch.setattr(win_inspector_mod.asyncio, 'sleep', fast_sleep)
    insp = WinInspectorSession(lambda: "$exe='C:\\x\\app.exe';")
    with pytest.raises(InspectorError):
        asyncio.run(insp.open(1))


# ── inspector: the target can go away mid-session (window reload) ────


class _FakeInspectorWs:
    """A scripted CDP websocket: answers each sent call with ``value``.

    ``close_after_enable`` makes it answer Runtime.enable and then
    report the socket CLOSED (a reload tearing the target down);
    ``binary_first`` sends one BINARY frame before the TEXT response.
    """

    def __init__(
        self, value=42, *, close_after_enable=False, binary_first=False
    ):
        self.value = value
        self.close_after_enable = close_after_enable
        self.binary_first = binary_first
        self._last = None
        self._answered_enable = False

    async def send_json(self, d):
        self._last = d

    async def receive(self):
        if self.close_after_enable and self._last['id'] == 1:
            return types.SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data='')
        if self.binary_first:
            self.binary_first = False
            return types.SimpleNamespace(
                type=aiohttp.WSMsgType.BINARY, data=b''
            )
        msg = {
            'id': self._last['id'],
            'result': {'result': {'value': self.value}},
        }
        return types.SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT, data=_json.dumps(msg)
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeInspectorSession:
    """``ws_connect(url)`` per a URL→behaviour map; URLs mapped to an
    exception raise it as the handshake would."""

    def __init__(self, behaviours):
        self.behaviours = behaviours
        self.connects: list[str] = []

    def ws_connect(self, url, **kw):
        self.connects.append(url)
        behaviour = self.behaviours[url]
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _handshake_400():
    try:
        return aiohttp.WSServerHandshakeError(
            request_info=None,
            history=(),
            status=400,
            message='gone',
            headers=None,
        )
    except TypeError:  # older aiohttp signature
        return aiohttp.WSServerHandshakeError(
            None, (), status=400, message='gone', headers=None
        )


async def test_evaluate_rediscovers_the_target_on_handshake_400(
    monkeypatch,
):
    """A window reload hands the inspector a NEW target id; the old URL
    answers the handshake with 400 (seen live driving a login).
    evaluate must re-discover the target and retry the call."""

    insp = win_inspector_mod.InspectorSession(lambda: "$exe='x';")
    insp._ws_url = 'ws://127.0.0.1:9229/old'
    session = _FakeInspectorSession({
        'ws://127.0.0.1:9229/old': _handshake_400(),
        'ws://127.0.0.1:9229/new': _FakeInspectorWs(value='OK'),
    })
    monkeypatch.setattr(
        win_inspector_mod.aiohttp, 'ClientSession', lambda *a, **kw: session
    )

    async def rediscover():
        return 'ws://127.0.0.1:9229/new'

    monkeypatch.setattr(insp, '_discover_ws', rediscover)
    assert await insp.evaluate('1+1') == 'OK'
    assert session.connects == [
        'ws://127.0.0.1:9229/old',
        'ws://127.0.0.1:9229/new',
    ]


async def test_evaluate_retries_when_the_socket_closes_mid_call(
    monkeypatch,
):
    """The socket can CLOSE between calls (the reload again): retry on
    the re-discovered target instead of surfacing a WS type error."""

    insp = win_inspector_mod.InspectorSession(lambda: "$exe='x';")
    insp._ws_url = 'ws://127.0.0.1:9229/old'
    session = _FakeInspectorSession({
        'ws://127.0.0.1:9229/old': _FakeInspectorWs(close_after_enable=True),
        'ws://127.0.0.1:9229/new': _FakeInspectorWs(value=7),
    })
    monkeypatch.setattr(
        win_inspector_mod.aiohttp, 'ClientSession', lambda *a, **kw: session
    )

    async def rediscover():
        return 'ws://127.0.0.1:9229/new'

    monkeypatch.setattr(insp, '_discover_ws', rediscover)
    assert await insp.evaluate('1+1') == 7


async def test_evaluate_skips_binary_frames(monkeypatch):
    """CDP may deliver BINARY frames; they are not the response — skip
    them instead of erroring (a receive_json on binary raised)."""
    insp = win_inspector_mod.InspectorSession(lambda: "$exe='x';")
    insp._ws_url = 'ws://127.0.0.1:9229/one'
    session = _FakeInspectorSession({
        'ws://127.0.0.1:9229/one': _FakeInspectorWs(
            value='text-after-binary', binary_first=True
        ),
    })
    monkeypatch.setattr(
        win_inspector_mod.aiohttp, 'ClientSession', lambda *a, **kw: session
    )
    assert await insp.evaluate('x') == 'text-after-binary'
    assert session.connects == ['ws://127.0.0.1:9229/one']


async def test_evaluate_gives_up_after_one_retry(monkeypatch):
    """Negative control: a target that stays stale fails with a clear
    InspectorError after ONE re-discovery, not an endless loop."""
    insp = win_inspector_mod.InspectorSession(lambda: "$exe='x';")
    insp._ws_url = 'ws://127.0.0.1:9229/old'
    session = _FakeInspectorSession({
        'ws://127.0.0.1:9229/old': _handshake_400(),
        'ws://127.0.0.1:9229/new': _handshake_400(),
    })
    monkeypatch.setattr(
        win_inspector_mod.aiohttp, 'ClientSession', lambda *a, **kw: session
    )

    async def rediscover():
        return 'ws://127.0.0.1:9229/new'

    monkeypatch.setattr(insp, '_discover_ws', rediscover)
    with pytest.raises(win_inspector_mod.InspectorError):
        await insp.evaluate('1+1')
    assert session.connects.count('ws://127.0.0.1:9229/new') == 1


# ── P4: inspector sessions collide machine-wide ─────────────────────


async def test_open_refuses_a_foreign_serving_port(monkeypatch, tmp_path):
    """9229 already serves a DIFFERENT process → refuse: never trigger,
    never close, typed InspectorBusy. (Two vendor clients / an outside
    probe share the one fixed port.)"""
    _fast_busy(monkeypatch, tmp_path)
    triggers = []

    async def spy_trigger(self, pid):
        triggers.append(pid)

    async def foreign_pid(self, ws_url=None):
        return 9999

    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_trigger', spy_trigger
    )
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_probe_pid', foreign_pid
    )

    async def already_serving(self):
        return 'ws://127.0.0.1:9229/devtools/browser/someone-else'

    monkeypatch.setattr(
        win_inspector_mod.InspectorSession,
        '_discover_ws',
        already_serving,
    )
    insp = WinInspectorSession(lambda: "$exe='x';")
    with pytest.raises(win_inspector_mod.InspectorBusy) as ei:
        await insp.open(4242)
    assert 'pid 9999' in str(ei.value)
    assert triggers == []  # never even tried to open our own
    assert insp._ws_url is None  # and never adopted theirs


async def test_open_reuses_a_leftover_inspector_on_the_target_pid(
    monkeypatch, tmp_path
):
    """One of OUR operations died between open and close → the target
    keeps its inspector on 9229. The busy check must not refuse
    forever (that killed auto-login until a client restart): a
    leftover on the VERY pid we were asked to debug is probed, adopted
    and reused — no second trigger."""
    _fast_busy(monkeypatch, tmp_path)
    triggers = []

    async def spy_trigger(self, pid):
        triggers.append(pid)

    async def leftover_pid(self, ws_url=None):
        return 4242  # the same process we want

    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_trigger', spy_trigger
    )
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_probe_pid', leftover_pid
    )

    async def leftover_serving(self):
        return 'ws://127.0.0.1:9229/devtools/browser/leftover'

    monkeypatch.setattr(
        win_inspector_mod.InspectorSession,
        '_discover_ws',
        leftover_serving,
    )
    insp = WinInspectorSession(lambda: "$exe='x';")
    await insp.open(4242)
    assert triggers == []  # reused — no second inspector opened
    assert insp._ws_url == 'ws://127.0.0.1:9229/devtools/browser/leftover'
    await insp.close()


async def test_open_refuses_an_unreadable_serving_port(monkeypatch, tmp_path):
    """A serving port whose pid cannot be read is NOT adopted (treat as
    unidentified, never as a match) — the conservative refusal."""
    _fast_busy(monkeypatch, tmp_path)
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession,
        '_probe_pid',
        mock.AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession,
        '_trigger',
        mock.AsyncMock(),
    )

    async def serving(self):
        return 'ws://127.0.0.1:9229/devtools/browser/opaque'

    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_discover_ws', serving
    )
    insp = WinInspectorSession(lambda: "$exe='x';")
    with pytest.raises(win_inspector_mod.InspectorBusy) as ei:
        await insp.open(4242)
    assert 'unidentified' in str(ei.value)
    assert insp._ws_url is None


async def test_open_pid_mismatch_never_closes_the_foreign_session(
    monkeypatch, tmp_path
):
    """We attached to someone ELSE'S inspector (an outside probe takes
    no lock of ours): drop the socket WITHOUT inspector.close() and
    raise the typed busy error — closing their session was the live
    incident."""
    _fake_lock(monkeypatch, tmp_path)
    monkeypatch.setattr(win_inspector_mod.InspectorSession, 'BUSY_RETRIES', 0)
    exprs = []

    async def recording_once(self, ws_url, expr, await_promise, timeout):
        exprs.append(expr)
        if expr == 'process.pid':
            return 9999  # a DIFFERENT process
        return None

    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_evaluate_once', recording_once
    )
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession,
        '_trigger',
        mock.AsyncMock(),
    )
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession,
        '_discover_ws',
        _not_busy_then_serving(),
    )
    insp = WinInspectorSession(lambda: "$exe='x';")
    with pytest.raises(win_inspector_mod.InspectorBusy) as ei:
        await insp.open(4242)
    assert 'pid 9999' in str(ei.value)
    closer = "require('inspector').close()"
    assert closer not in exprs  # never closed their session
    assert insp._ws_url is None  # disconnected


async def test_open_pid_match_proceeds(monkeypatch, tmp_path):
    """Negative control: the SAME pid → the session opens and stays
    attached (the checks guard, they do not block the happy path)."""
    _fast_busy(monkeypatch, tmp_path)

    async def same_pid(self, ws_url=None):
        return 4242

    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_probe_pid', same_pid
    )
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession,
        '_trigger',
        mock.AsyncMock(),
    )
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession,
        '_discover_ws',
        _not_busy_then_serving(),
    )
    insp = WinInspectorSession(lambda: "$exe='x';")
    await insp.open(4242)
    assert insp._ws_url is not None
    await insp.close()


async def test_open_serialises_two_sessions_machine_wide(monkeypatch, tmp_path):
    """Two concurrent open→close cycles (two vendors, two instances)
    take the machine-wide lock in turn: one trigger fully finishes
    before the other starts — proved with the REAL lock on the REAL
    filesystem. Without the lock the two 0.1 s triggers interleave."""
    _fast_busy(monkeypatch, tmp_path)
    events = []

    async def slow_trigger(self, pid):
        self._asked_pid = pid  # the probe must confirm exactly this
        events.append(f'start-{pid}')
        await asyncio.sleep(0.1)
        events.append(f'end-{pid}')

    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_trigger', slow_trigger
    )

    async def per_session_discover(self):
        # Not-busy on each session's FIRST call (the busy check), the
        # target on later ones.
        if not getattr(self, '_served', False):
            self._served = True
            return None
        return 'ws://127.0.0.1:9229/devtools/browser/x'

    monkeypatch.setattr(
        win_inspector_mod.InspectorSession,
        '_discover_ws',
        per_session_discover,
    )

    async def ok_pid(self, ws_url=None):
        return getattr(self, '_asked_pid', None)

    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_probe_pid', ok_pid
    )

    async def cycle(insp, pid):
        await insp.open(pid)
        await insp.close()  # releases the machine lock

    a = WinInspectorSession(lambda: "$exe='a';")
    b = WinInspectorSession(lambda: "$exe='b';")
    await asyncio.gather(cycle(a, 1), cycle(b, 2))
    # Serialized: each span is contiguous — no start between another
    # start and its end — and BOTH sessions ran.
    current = None
    started = set()
    for e in events:
        kind, pid = e.split('-', 1)
        if kind == 'start':
            assert current is None, f'interleaved triggers: {events}'
            current = pid
            started.add(pid)
        else:
            assert current == pid, f'interleaved triggers: {events}'
            current = None
    assert started == {'1', '2'}
    assert current is None  # every span closed


async def test_open_busy_retries_are_bounded(monkeypatch, tmp_path):
    """A persistently-busy port fails after the BOUNDED retries, proved
    by counting attempts — never by measuring wall-clock seconds (the
    seconds-based version took 60 s and failed under full-suite load;
    the unit tier is under 10 s)."""
    _fast_busy(monkeypatch, tmp_path)
    attempts = {'n': 0}

    async def always_busy(self):
        attempts['n'] += 1
        return 'ws://127.0.0.1:9229/devtools/browser/x'

    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_discover_ws', always_busy
    )
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession,
        '_probe_pid',
        mock.AsyncMock(return_value=None),  # unreadable → busy
    )
    insp = WinInspectorSession(lambda: "$exe='x';")
    with pytest.raises(win_inspector_mod.InspectorBusy):
        await insp.open(1)
    assert attempts['n'] == (
        win_inspector_mod.InspectorSession.BUSY_RETRIES + 1
    )


# ── P9: the engine refuses a non-Chromium CDP endpoint ──────────────


class _VersionServer:
    """A local /json/version answering a CONFIGURABLE Browser family —
    including Firefox's partial CDP (Remote Agent), which also serves a
    webSocketDebuggerUrl."""

    def __init__(self, browser: str):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 — http.server's name
                if self.path != '/json/version':
                    self.send_error(404)
                    return
                port = self.server.server_address[1]
                body = _json.dumps({
                    'Browser': outer.browser,
                    'webSocketDebuggerUrl': (
                        f'ws://127.0.0.1:{port}/devtools/browser/x'
                    ),
                }).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        outer.browser = browser
        outer.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=outer.server.serve_forever, daemon=True).start()
        outer.port = outer.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class _P9Client(_RecordingClient):
    """Records close_store so the refusal path can be asserted."""

    def __init__(self, cdp_port):
        super().__init__(cdp_port=cdp_port)
        self.closed_refs: list[str] = []
        self.after_opens: list[int] = []

    async def close_store(self, ref: str) -> None:
        self.closed_refs.append(ref)


async def test_firefox_cdp_endpoint_is_refused_and_closed(_mocked_proxy):
    """A Firefox kernel CAN expose a partial CDP (Remote Agent — seen
    live): reachable is not drivable. The engine must refuse with
    UnsupportedKernel, close the store, and never run after_open or
    start the proxy."""
    ff = _VersionServer('Firefox/100.0.2')
    try:
        client = _P9Client(ff.port)
        with pytest.raises(UnsupportedKernel) as ei:
            await _backend(client).start(_cfg())
        assert 'Firefox/100.0.2' in str(ei.value)
        assert client.closed_refs == ['store-a']  # the env was closed
        assert client.after_opens == []  # the entry step never ran
        _mocked_proxy.return_value.start.assert_not_called()
    finally:
        ff.stop()


async def test_chromium_family_browsers_proceed(_mocked_proxy, tmp_path):
    """Chrome and Edg kernels front the proxy as before (the family
    check gates, it does not block the happy path)."""
    for browser in ('Chrome/135.0.0.0', 'Edg/131.0.0.0'):
        srv = _VersionServer(browser)
        try:
            client = _P9Client(srv.port)
            with mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path):
                info = await _backend(client).start(_cfg())
            assert info.cdp_port == srv.port
            assert client.closed_refs == []
            _mocked_proxy.return_value.start.assert_awaited()
            _mocked_proxy.return_value.start.reset_mock()
        finally:
            srv.stop()
