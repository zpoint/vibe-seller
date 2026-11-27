"""The inject-engine vendor contract: StoreRef, after_open, error codes.

These pin the pieces PR "inject contract" adds so later vendor adapters
can rely on them: the engine calls ``after_open`` exactly once, after
CDP is verified and before the proxy fronts the env, and never when the
open failed; ``StoreRef``'s stable-ref fields default safely; every new
error class carries its taxonomy code and a vendor-neutral hint.
"""

import asyncio
import json as _json
from pathlib import Path
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
    cdp_probe = mock.AsyncMock(return_value=True)
    with (
        mock.patch.object(InjectCdpBackend, '_cdp_reachable', new=cdp_probe),
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
            '_cdp_reachable',
            new=mock.AsyncMock(return_value=False),
        ),
        mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path),
    ):
        await _backend(client).start(_cfg())
    assert client.after_open_calls == 0
    _mocked_proxy.return_value.start.assert_not_called()


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
    # unavailable store keeps its flag (the UI greys it out).
    ref = StoreRef('demo-store', ref='100000000001', available=False)
    assert ref.open_ref == '100000000001'
    assert ref.available is False
    assert ref.name == 'demo-store'


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


def test_shared_inspector_uses_any_exe_script_callable(monkeypatch):
    """The shared session needs no client knowledge beyond the callable
    that resolves ``$exe`` — any vendor can supply it."""
    scripts = []

    def fake_run(script, *a, **kw):
        scripts.append(script)
        return 'ok'

    async def fake_discover(self):
        return 'ws://127.0.0.1:9229/devtools/browser/x'

    monkeypatch.setattr(win_inspector_mod, '_run_inspector_ps', fake_run)
    monkeypatch.setattr(
        win_inspector_mod.InspectorSession, '_discover_ws', fake_discover
    )
    insp = WinInspectorSession(lambda: "$exe='C:\\x\\app.exe';")
    asyncio.run(insp.open(4242))
    assert len(scripts) == 1
    assert "$exe='C:\\x\\app.exe';" in scripts[0]
    assert 'process._debugProcess(4242)' in scripts[0]
    assert "$env:ELECTRON_RUN_AS_NODE='1';" in scripts[0]


def test_shared_inspector_raises_when_discovery_fails(monkeypatch):
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
