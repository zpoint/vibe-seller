"""C4: a stale stop tears down only what THIS server opened.

Live evidence (e1, native Windows): a server session row survives the
vendor closing the kernel (the user closed the store window, or the
client restarted); the user then opens the profile BY HAND — a fresh
kernel. The next ``browser/start`` stops the stale session, and the
engine's ``stop()`` used to call the vendor's ``close_store(ref)``
by bare profile id — closing the hand-opened kernel — so the fresh
start reopened the profile on a NEW kernel and the user's window was
gone.

The invariant pinned here, per scenario:
- stale row + the profile now open on a DIFFERENT kernel → no
  ``close_store``, and the fresh start attaches to the open one;
- stale row + our own kernel still running → we close it (healthy
  path unchanged);
- no stale row → attach (unchanged);
- a vendor that declares ``reopens_hand_opened`` may close even on
  the stale path (yuehulian: only its own close clears a dead-env
  row in its client).

The kernel identity is the endpoint recorded at open plus the
kernel's browser GUID (the ``webSocketDebuggerUrl`` tail of
``/json/version``) — the same identity the CDP mux records for its
upstream.
"""

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import threading
from unittest import mock

import pytest

from app.browser.adapters.yuehulian import YUEHULIAN_VENDOR
from app.browser.base import BrowserSessionInfo
from app.browser.inject import engine as engine_mod
from app.browser.inject.client import InjectClient, OpenedStore, StoreRef
from app.browser.inject.engine import InjectCdpBackend
from app.browser.inject.errors import CdpUnreachable
from app.browser.vendors import (
    CARDINALITY_SINGLE,
    BrowserVendorSpec,
    VendorCredentialField,
)

pytestmark = pytest.mark.unit


class _Kernel:
    """A fake vendor kernel: answers ``/json/version`` with a
    CONFIGURABLE browser GUID (the per-kernel identity), and can die
    (the vendor closed it / the client restarted)."""

    def __init__(self, guid: str, ws_path: str = '/devtools/browser/'):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 — http.server's name
                if self.path != '/json/version':
                    self.send_error(404)
                    return
                port = self.server.server_address[1]
                body = json.dumps({
                    'Browser': 'Chrome/135.0.0.0',
                    'webSocketDebuggerUrl': (
                        f'ws://127.0.0.1:{port}{outer.ws_path}{outer.guid}'
                    ),
                }).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        outer.guid = guid
        outer.ws_path = ws_path
        outer.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=outer.server.serve_forever, daemon=True).start()
        outer.port = outer.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class _StaleClient(InjectClient):
    """Records closes; ``open_port`` is the endpoint the vendor's open
    answers with (its existing kernel when the profile is open — the
    attach contract). ``was_open`` implements ``env_was_open`` (None =
    no signal, the default client); ``fail_after_open`` makes
    ``after_open`` raise, a vendor failure AFTER the open — what
    #349's start-failure funnel cleans up."""

    def __init__(self):
        self.open_port = 0
        self.opens: list[str] = []
        self.closed: list[str] = []
        self.fail_after_open: Exception | None = None

    async def ensure_running(self) -> None:
        return None

    async def is_logged_in(self) -> bool:
        return True

    async def list_stores(self) -> list[StoreRef]:
        return [StoreRef('store-a')]

    async def open_store(self, ref: str) -> OpenedStore:
        self.opens.append(ref)
        return OpenedStore('127.0.0.1', self.open_port)

    async def after_open(self, opened: OpenedStore) -> None:
        if self.fail_after_open is not None:
            raise self.fail_after_open

    async def close_store(self, ref: str) -> None:
        self.closed.append(ref)


class _HookedClient(_StaleClient):
    """A client that IMPLEMENTS env_was_open (the R2-1 contract):

    ``was_open`` True/False is the answer, ``hook_error`` raises (the
    vendor's status API cannot answer), ``hook_hangs`` stalls past the
    engine's bound."""

    def __init__(self):
        super().__init__()
        self.was_open = False
        self.hook_error: Exception | None = None
        self.hook_hangs = False

    async def env_was_open(self, ref: str) -> bool | None:
        if self.hook_hangs:
            await asyncio.sleep(30)
        if self.hook_error is not None:
            raise self.hook_error
        return self.was_open


def _cfg(**extra) -> dict:
    cfg = {'browser_ref': 'store-a', 'proxy_port': 9299, 'store_slug': 's'}
    cfg.update(extra)
    return cfg


class _FastProbeBackend(InjectCdpBackend):
    """The start path's CDP probe with zero retry cost — every kernel
    here is a live local server (first attempt answers) or provably
    dead (nothing restarts it mid-test), so the retry budget only
    costs wall-clock: one attempt, no delay."""

    _CDP_ATTEMPTS = 1
    _CDP_DELAY = 0.0


def _backend(client: _StaleClient) -> InjectCdpBackend:
    return _FastProbeBackend(client_factory=lambda cfg: client)


@contextmanager
def _mocked_proxy(tmp_path):
    """CDPMuxProxy mocked (records start/stop) + downloads in tmp."""
    with (
        mock.patch.object(engine_mod, 'CDPMuxProxy') as proxy,
        mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path),
    ):
        proxy.return_value.start = mock.AsyncMock()
        proxy.return_value.stop = mock.AsyncMock()
        yield proxy


def _recording_close(backend: InjectCdpBackend) -> mock.AsyncMock:
    """Record CDP Browser.close calls instead of making them (the
    kernels here are plain HTTP servers; the close itself is
    best-effort and covered by the healthy-path assertions)."""
    closer = mock.AsyncMock()
    backend._close_browser = closer
    return closer


# ── e1: the stale stop must not close a hand-opened kernel ──────────


async def test_stale_stop_leaves_a_hand_opened_kernel_alone(tmp_path):
    """The live bug (e1): the session row survived the vendor closing
    OUR kernel; the profile was then opened BY HAND on a different
    port. The stale stop must not ``close_store`` (that call closes
    whatever kernel the profile has NOW), and the fresh start attaches
    to the hand-opened kernel's endpoint — no reopen."""
    ours = _Kernel('opened-by-server')
    hand = _Kernel('opened-by-hand')
    client = _StaleClient()
    client.open_port = ours.port
    backend = _backend(client)
    closer = _recording_close(backend)
    try:
        with _mocked_proxy(tmp_path) as proxy:
            await backend.start(_cfg())
            # The vendor closed our kernel; the user hand-opened the
            # profile (a fresh kernel the vendor now reports for it).
            ours.stop()
            client.open_port = hand.port
            await backend.stop(BrowserSessionInfo())
            # The fresh start — what ensure_session does right after
            # the stale stop, on a NEW backend like the manager's.
            attached = await _backend(client).start(_cfg())
        assert client.closed == []  # never closed the profile
        assert closer.await_count == 0  # nor sent Browser.close anywhere
        assert client.opens == ['store-a', 'store-a']  # open = attach
        assert attached.cdp_port == hand.port  # SAME kernel, not a new one
        assert proxy.return_value.start.await_count == 2
        assert proxy.return_value.stop.await_count == 1  # our proxy went
    finally:
        hand.stop()


async def test_stale_stop_never_closes_a_reused_port(tmp_path):
    """Twist on the same surface: the recorded endpoint is ALIVE but
    now serves a DIFFERENT kernel (the port was reused — another
    instance). Identity mismatch ⇒ close NEITHER the kernel nor the
    profile."""
    reused = _Kernel('first-kernel')
    client = _StaleClient()
    client.open_port = reused.port
    backend = _backend(client)
    closer = _recording_close(backend)
    try:
        with _mocked_proxy(tmp_path) as proxy:
            await backend.start(_cfg())
            reused.guid = 'another-kernel-took-the-port'
            await backend.stop(BrowserSessionInfo())
        assert client.closed == []
        assert closer.await_count == 0
        assert proxy.return_value.stop.await_count == 1  # proxy still ours
    finally:
        reused.stop()


async def test_healthy_stop_closes_the_kernel_we_opened(tmp_path):
    """Negative control: OUR kernel still running on the recorded
    endpoint, identity matching ⇒ the healthy teardown is unchanged —
    CDP Browser.close on our endpoint, then the vendor's close_store,
    then the proxy."""
    ours = _Kernel('opened-by-server')
    client = _StaleClient()
    client.open_port = ours.port
    backend = _backend(client)
    closer = _recording_close(backend)
    try:
        with _mocked_proxy(tmp_path) as proxy:
            await backend.start(_cfg())
            await backend.stop(BrowserSessionInfo())
        assert closer.await_count == 1  # closed OUR kernel...
        assert closer.await_args.args == ('127.0.0.1', ours.port)
        assert client.closed == ['store-a']  # ...and the env, as before
        assert proxy.return_value.stop.await_count == 1
    finally:
        ours.stop()


async def test_alive_without_a_parseable_guid_is_still_ours(tmp_path):
    """Compat fallback: a kernel whose /json/version carries no
    ``/devtools/browser/`` GUID (an older session, or a client that
    reports none) is identified by endpoint liveness alone — the
    pre-C4 healthy behaviour, so such sessions still tear down."""
    odd = _Kernel('ignored', ws_path='/nonstandard/')
    client = _StaleClient()
    client.open_port = odd.port
    backend = _backend(client)
    closer = _recording_close(backend)
    try:
        with _mocked_proxy(tmp_path) as proxy:
            await backend.start(_cfg())
            assert backend._browser_guid is None  # nothing was parsed
            await backend.stop(BrowserSessionInfo())
        assert closer.await_count == 1
        assert client.closed == ['store-a']
        assert proxy.return_value.stop.await_count == 1
    finally:
        odd.stop()


# ── no stale row ⇒ attach (the documented scenario-6 contract) ──────


async def test_no_stale_row_attaches_to_the_hand_opened_kernel(tmp_path):
    """With no prior server session, a hand-opened profile attaches on
    the SAME port: one open call (the vendor's open answers with the
    existing kernel), zero closes, zero reopens."""
    hand = _Kernel('opened-by-hand')
    client = _StaleClient()
    client.open_port = hand.port
    try:
        with _mocked_proxy(tmp_path):
            info = await _backend(client).start(_cfg())
        assert info.cdp_port == hand.port
        assert client.opens == ['store-a']
        assert client.closed == []
    finally:
        hand.stop()


# ── the reopens_hand_opened escape hatch ────────────────────────────


async def test_reopens_declarant_closes_even_on_a_stale_stop(tmp_path):
    """A vendor whose spec declares ``reopens_hand_opened`` (yuehulian:
    its client keeps listing a dead env as Opened, and only its own
    close clears that) still gets its close_store on the stale path —
    the flag is the ONLY way through the guard."""
    ours = _Kernel('opened-by-server')
    client = _StaleClient()
    client.open_port = ours.port
    backend = _backend(client)
    closer = _recording_close(backend)
    try:
        with _mocked_proxy(tmp_path) as proxy:
            await backend.start(_cfg(reopens_hand_opened=True))
            ours.stop()  # the kernel we opened is gone
            await backend.stop(BrowserSessionInfo())
        # No kernel to Browser.close (the endpoint is dead), but the
        # vendor's close DID run — it is what clears the client state.
        assert closer.await_count == 0
        assert client.closed == ['store-a']
        assert proxy.return_value.stop.await_count == 1
    finally:
        ours.stop()


# ── R1: the dead-kernel recovery path (scenarios 8/9) ───────────────
#
# Our kernel dies on its own (the client was killed mid-task, or the
# user closed the store window mid-task) and the session row survives.
# Pre-C4, the stale stop's close_store(ref) also cleared VENDOR-side
# state; post-C4 a dead endpoint skips it. These pin that the NEXT
# start still recovers, per vendor behaviour.


async def test_dead_kernel_honest_client_next_start_opens_fresh(tmp_path):
    """Scenario 8/9, a client that notices its kernel died (the
    profile is listed CLOSED after the crash): the stale stop skips
    close_store — a no-op on an already-closed profile, exactly like
    pre-C4 — and the next start recovers through the vendor's own
    open: open_store answers with a FRESH kernel's endpoint."""
    ours = _Kernel('opened-by-server')
    fresh = _Kernel('reopened-by-vendor')
    client = _StaleClient()
    client.open_port = ours.port
    backend = _backend(client)
    closer = _recording_close(backend)
    try:
        with _mocked_proxy(tmp_path):
            await backend.start(_cfg())
            ours.stop()  # the kernel died on its own
            client.open_port = fresh.port  # the client now opens fresh
            await backend.stop(BrowserSessionInfo())
            recovered = await _backend(client).start(_cfg())
        assert client.closed == []  # nothing to clear — already closed
        assert closer.await_count == 0
        assert recovered.cdp_port == fresh.port  # a fresh kernel
        assert client.opens == ['store-a', 'store-a']
    finally:
        fresh.stop()


async def test_dead_kernel_stale_lister_with_flag_recovers(tmp_path):
    """Scenario 8/9, a client that KEEPS listing a dead env as Opened
    (yuehulian's documented behaviour — only its own close clears the
    row): such a vendor declares ``reopens_hand_opened``, so the stale
    stop STILL clears the listing through close_store, and the next
    start opens a fresh kernel. The declaration is what keeps the
    dead-kernel recovery working for stale-listing clients."""
    ours = _Kernel('opened-by-server')
    fresh = _Kernel('reopened-by-vendor')
    client = _StaleClient()
    client.open_port = ours.port
    backend = _backend(client)
    _recording_close(backend)
    try:
        with _mocked_proxy(tmp_path):
            await backend.start(_cfg(reopens_hand_opened=True))
            ours.stop()  # the kernel died; the client still lists Opened
            client.open_port = fresh.port
            await backend.stop(BrowserSessionInfo())
            recovered = await _backend(client).start(_cfg())
        assert client.closed == ['store-a']  # the listing was cleared
        assert recovered.cdp_port == fresh.port
    finally:
        fresh.stop()


async def test_dead_endpoint_reported_open_is_refused_visibly(tmp_path):
    """Out of contract — a vendor whose client reports a DEAD endpoint
    for an "open" profile but did NOT declare ``reopens_hand_opened``:
    the engine refuses the start with ``CdpUnreachable`` (the
    documented contract: an env whose debug endpoint never answers is
    never driven; the start route answers HTTP 500 'Failed to start
    browser: … never answered'). Since #349's start-failure funnel
    (kept, ownership-routed — REVIEW-MAIN-349 case (a)), the env the
    vendor just "opened" for us is then CLOSED: this client has no
    ``env_was_open`` signal (unknown ⇒ treated as ours), and a
    just-opened env whose CDP never answered is exactly the wedge the
    funnel exists to clear. A vendor whose open ATTACHES to a live
    hand-opened env is the protected case — pinned by the
    ``test_failed_start_*funnel*`` tests below."""
    ours = _Kernel('opened-by-server')
    client = _StaleClient()
    client.open_port = ours.port
    backend = _backend(client)
    try:
        with _mocked_proxy(tmp_path):
            await backend.start(_cfg())
            ours.stop()  # died mid-task; the client keeps the row Open
            await backend.stop(BrowserSessionInfo())
            # open_store hands back the dead endpoint again — the
            # engine refuses to drive it, and the funnel clears the
            # wedged (assumed-ours) env.
            with pytest.raises(CdpUnreachable):
                await _backend(client).start(_cfg())
        assert client.closed == ['store-a']
    finally:
        ours.stop()


# ── #349 integration: the start-failure funnel is ownership-aware ───
#
# REVIEW-MAIN-349.md: main's _abandon_open_env closes the env on ANY
# failed start — including one that ATTACHED to an env the user
# opened by hand. Routed through the ONE predicate: attached → close
# nothing vendor-side; spawned → closed (the funnel's wedge cleanup).


async def test_failed_start_after_our_open_closes_env(tmp_path):
    """Case (a): the open SPAWNED the kernel (the vendor's
    env_was_open says it was not running) and the start then fails —
    the funnel closes the env (Browser.close + close_store) and stops
    our half-started proxy, and the ORIGINAL error propagates. This
    is #349's contract, kept by the ownership routing."""
    ours = _Kernel('spawned-by-open')
    client = _HookedClient()
    client.open_port = ours.port
    client.was_open = False  # the vendor spawned it for our open
    backend = _backend(client)
    closer = _recording_close(backend)
    try:
        with _mocked_proxy(tmp_path) as proxy:
            # A post-open failure DEEP in the funnel's coverage: the
            # proxy never comes up.
            proxy.return_value.start = mock.AsyncMock(
                side_effect=RuntimeError('proxy bind failed')
            )
            with pytest.raises(RuntimeError, match='proxy bind'):
                await backend.start(_cfg())
        assert closer.await_count == 1  # OUR kernel was closed...
        assert closer.await_args.args == ('127.0.0.1', ours.port)
        assert client.closed == ['store-a']  # ...and the env
        assert proxy.return_value.stop.await_count == 1  # our proxy
    finally:
        ours.stop()


async def test_failed_start_after_attach_closes_nothing_of_the_users(
    tmp_path,
):
    """Case (b): the open ATTACHED to an env that was already open
    (env_was_open True — a profile the user opened by hand) and the
    start then fails: no Browser.close, no close_store — only our own
    half-started proxy is stopped, and the original error
    propagates."""
    hand = _Kernel('opened-by-hand')
    client = _HookedClient()
    client.open_port = hand.port
    client.was_open = True  # the vendor attached
    backend = _backend(client)
    closer = _recording_close(backend)
    try:
        with _mocked_proxy(tmp_path) as proxy:
            proxy.return_value.start = mock.AsyncMock(
                side_effect=RuntimeError('proxy bind failed')
            )
            with pytest.raises(RuntimeError, match='proxy bind'):
                await backend.start(_cfg())
        assert closer.await_count == 0  # the user's kernel untouched
        assert client.closed == []  # ...and their env
        assert proxy.return_value.stop.await_count == 1  # ours stopped
    finally:
        hand.stop()


async def test_cancelled_start_after_attach_closes_nothing_of_the_users(
    tmp_path,
):
    """Case (c): the same attach, CANCELLED mid-start (a task stop or
    a server shutdown reaching the start): the funnel catches the
    cancellation, closes nothing of the user's, stops our proxy, and
    the cancellation propagates."""

    hand = _Kernel('opened-by-hand')
    client = _HookedClient()
    client.open_port = hand.port
    client.was_open = True
    backend = _backend(client)
    closer = _recording_close(backend)
    try:
        with _mocked_proxy(tmp_path) as proxy:

            async def slow_start():
                await asyncio.sleep(30)  # cancelled in here

            proxy.return_value.start = slow_start
            task = asyncio.create_task(backend.start(_cfg()))
            await asyncio.sleep(0.3)  # let it reach the proxy start
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert closer.await_count == 0
        assert client.closed == []
        assert proxy.return_value.stop.await_count == 1
    finally:
        hand.stop()


# ── R2-2: when the vendor's status API cannot answer, NOT ours ─────
#
# Losing a user's hand-opened window is worse than leaving an env
# open: an env_was_open that raises (or hangs past the engine's
# bound) counts as NOT ours — the funnel stops only our proxy, sends
# no Browser.close and no close_store, and logs ONE WARNING naming
# the vendor + profile ref.


async def test_env_state_error_means_not_ours(tmp_path, caplog):
    """The status call RAISES: not ours — no Browser.close, no
    close_store, our proxy stopped, ONE warning naming the client and
    the ref, and the original start error propagates."""
    ours = _Kernel('vendor-kernel')
    client = _HookedClient()
    client.open_port = ours.port
    client.hook_error = RuntimeError('status API down')
    backend = _backend(client)
    closer = _recording_close(backend)
    try:
        with (
            _mocked_proxy(tmp_path) as proxy,
            caplog.at_level(
                logging.WARNING, logger='app.browser.inject.engine'
            ),
        ):
            proxy.return_value.start = mock.AsyncMock(
                side_effect=RuntimeError('proxy bind failed')
            )
            with pytest.raises(RuntimeError, match='proxy bind'):
                await backend.start(_cfg())
        assert closer.await_count == 0
        assert client.closed == []
        assert proxy.return_value.stop.await_count == 1
        warnings = [
            r
            for r in caplog.records
            if r.levelname == 'WARNING' and 'env_was_open failed' in r.message
        ]
        assert len(warnings) == 1
        assert '_HookedClient' in warnings[0].message
        assert 'store-a' in warnings[0].message
    finally:
        ours.stop()


async def test_env_state_timeout_means_not_ours(tmp_path, caplog):
    """The status call HANGS past the engine's bound (shrunk here):
    the same — not ours, nothing vendor-side closed, one warning."""
    ours = _Kernel('vendor-kernel')
    client = _HookedClient()
    client.open_port = ours.port
    client.hook_hangs = True
    backend = _backend(client)
    backend._ENV_STATE_TIMEOUT_S = 0.2
    closer = _recording_close(backend)
    try:
        with (
            _mocked_proxy(tmp_path) as proxy,
            caplog.at_level(
                logging.WARNING, logger='app.browser.inject.engine'
            ),
        ):
            proxy.return_value.start = mock.AsyncMock(
                side_effect=RuntimeError('proxy bind failed')
            )
            with pytest.raises(RuntimeError, match='proxy bind'):
                await backend.start(_cfg())
        assert closer.await_count == 0
        assert client.closed == []
        assert proxy.return_value.stop.await_count == 1
        warnings = [
            r
            for r in caplog.records
            if r.levelname == 'WARNING'
            and 'env_was_open timed out' in r.message
        ]
        assert len(warnings) == 1
    finally:
        ours.stop()


def test_yuehulian_is_the_reopens_declarer():
    """yuehulian declares the escape hatch (its client lists dead envs
    as Opened); every other spec defaults to ATTACH."""
    assert YUEHULIAN_VENDOR.reopens_hand_opened is True
    plain = BrowserVendorSpec(
        key='other-vendor',
        display_name_en='Other',
        display_name_zh='其他',
        account_cardinality=CARDINALITY_SINGLE,
        credential_fields=(
            VendorCredentialField('acct', 'vendors.other.acct'),
        ),
    )
    assert plain.reopens_hand_opened is False
