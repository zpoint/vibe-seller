"""AdsPower (AdsPower Global) anti-detect browser backend.

AdsPower is the textbook local-API vendor: list / start / stop /
active / local-active on ``http://127.0.0.1:<port>`` with a Bearer API
key, and even login has a documented command-line form (headless with
the key). No GUI injection, no Electron inspector, no ``after_open`` —
the start call is made with ``ip_tab=0&open_tabs=0`` so the browser
opens straight on the profile's platform URL (verified live). From the
CDP endpoint onward it is identical to the ziniao / chrome backends
(CDPMuxProxy → browser-use).

The V1 endpoints are used (status, user/list, browser/start,
browser/stop); the V2 equivalents (browser-profile/list, /start, /stop
with ``profile_id`` / ``last_opened_tabs`` / ``proxy_detection``)
behave the same and are named here for the future.

Three AdsPower facts shape this module (all verified live, FACTS.md in
~/adspower-glm):

- **The port is discovered, never configured.** The default 50325 is
  only used when free (Hyper-V/WSL port exclusions can reserve it
  forever), so every launch lands on a different port. The client
  writes the CURRENT base URL to its ``local_api`` file; the port is
  re-read when a call cannot connect (the file can be stale after a
  client restart).
- **The API key must belong to the account signed into the client.**
  A key of a different team member answers ``API Key mismatch`` (an
  ``AccountMismatch``). Signed out entirely, the API answers nothing —
  the Local API only starts at login (GUI or headless-with-key).
- **Kernel states differ**: ``is updating`` (a download IS running,
  ready in ~40 s on Windows GUI) vs ``is not ready`` (the kernel is
  MISSING and nothing downloads it from a background session — seen
  on macOS). They get different, actionable hints (R11).

What the shared :class:`~app.browser.local_api_client.LocalApiClient`
base cannot express stays here as overrides (the review's list):

- **D4 ``ensure_running``**: probe → launch the GUI (remember-me) →
  a 30 s grace → headless with the API key.
- **D7 failed-start decision**: an empty endpoint is ambiguous
  (Firefox kernel vs orphaned SunBrowser) — kernel process evidence
  decides; only THIS profile's tree is killed.
- **R11 kernel states** and the bounded rate-limit resend; **R16**
  the not-authenticated wordings (``adspower_common``'s constants).
"""

import asyncio
import logging
import ntpath
import re
import time
from urllib.parse import urlsplit

from app.browser.adapters import adspower_mac, adspower_win
from app.browser.adapters.adspower_common import (
    DEFAULT_API_PORT,
    NO_KEY_ADVICE_EN,
    NO_KEY_ADVICE_ZH,
    SIGN_IN_NEEDED_EN,
    SIGN_IN_NEEDED_ZH,
    UNSUPPORTED_KERNEL_TEXT,
    KernelProcess,
)
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
from app.browser.ziniao_utils import get_platform

logger = logging.getLogger(__name__)


def _platform_layer_module():
    """The platform-specific layer module — the ONE picker.

    Windows AND WSL drive the Windows side over 127.0.0.1 (mirrored
    networking); macOS uses its own layer. The rest of this module is
    platform-neutral and only talks to the layer's duck-typed surface:
    ``make_process_layer`` / ``read_local_api_port`` /
    ``list_kernel_processes`` / ``kill_process_tree``.
    """
    if get_platform() == 'mac':
        return adspower_mac
    return adspower_win


class AdsPowerClient(LocalApiClient):
    """Drives the real AdsPower client through its local HTTP API."""

    # ── the LocalApiClient uniform parts ────────────────────────────
    vendor_key = 'adspower'
    api_port = DEFAULT_API_PORT
    # The port comes from the client's own local_api file (a URL, not
    # a bare port — hence the discover_port override); 'file' tells the
    # base's default not to trust api_port alone.
    discovery = 'file'
    auth = 'bearer'
    auth_field = 'api_key'
    list_method = 'GET'
    open_method = 'GET'
    close_method = 'GET'
    # One call ≥1.2 s apart (the documented limit is 2 req/s below 200
    # profiles, but the SECOND immediate call already answers "Too many
    # request per second" — verified live).
    min_interval_s = 1.2
    connect_timeout_s = 5.0
    request_timeout_s = 20.0
    max_list_pages = 200

    # ── vendor constants the base has no attribute for ──────────────
    #: user/list's documented maximum page size (shrinkable in tests).
    LIST_PAGE_SIZE = 100
    #: Bounded waits for the launched GUI / headless service.
    GUI_WAIT_S = 90.0
    HEADLESS_WAIT_S = 45.0
    #: Grace for a shell SOMEONE ELSE just started (the user, Windows
    #: autostart): remember-me sign-in + the node service need time
    #: before the Local API listens. Only after this does "shell up,
    #: API down" mean signed out (R3).
    SIGNED_OUT_GRACE_S = 30.0
    LAUNCH_POLL_S = 2.5
    #: Kernel download: first use of a kernel version downloads it
    #: ("SunBrowser NNN is updating" / "is not ready").
    KERNEL_POLL_S = 5.0
    KERNEL_TIMEOUT_S = 180.0
    #: ``is not ready`` brief poll (R11): nothing downloads the kernel
    #: from a background session (verified live on macOS), so waiting
    #: the full 180 s would only stall the task.
    KERNEL_NOT_READY_WAIT_S = 30.0
    #: Rate-limit resend budget (another consumer may be calling); the
    #: base retries once per api_call, this bounds the extra rounds.
    RATE_LIMIT_RETRIES = 3

    # API paths (V1; V2 equivalents named in the module docstring).
    P_STATUS = '/status'
    P_LIST = '/api/v1/user/list'
    P_START = '/api/v1/browser/start'
    P_STOP = '/api/v1/browser/stop'

    #: The rate-limit body message (HTTP 200, code -1).
    MSG_RATE_LIMIT = 'Too many request per second'
    #: ``is updating`` — a download IS running (Windows GUI: ready in
    #: ~40 s); poll bounded, hint says "downloading, retry".
    MSG_KERNEL_UPDATING = 'is updating'
    #: ``is not ready`` — the kernel is MISSING and, verified live on
    #: macOS (2026-11-27), nothing downloads it from a background/
    #: headless session even after minutes of retries. Poll briefly,
    #: then fail with the actionable hint (open AdsPower and get the
    #: kernel, or re-pin the profile in the client).
    MSG_KERNEL_NOT_READY = 'is not ready'

    def __init__(self, browser_config: dict | None = None):
        super().__init__(browser_config)
        cfg = self._cfg
        # An explicitly configured api_port (tests, a pinned setup) is
        # operator intent: transport failures on it are real failures,
        # not a stale local_api file to re-read.
        self._port_pinned = bool(cfg.get('api_port'))
        layer = _platform_layer_module()
        self._proc = layer.make_process_layer(cfg.get('client_path') or None)
        # Platform seams (overridden in tests):
        #: port discovery, from the platform's local_api file.
        self._read_port = layer.read_local_api_port
        #: kernel process listing, from the platform's process source.
        self._list_kernels = layer.list_kernel_processes
        #: one-profile kernel tree kill, platform-specific.
        self._kill_tree = layer.kill_process_tree

    # ── the LocalApiClient vendor parts ─────────────────────────────

    def list_request(self, page: int) -> tuple[str, dict | None]:
        """user/list, 1-based page of LIST_PAGE_SIZE rows."""
        return (
            f'{self.P_LIST}?page={page + 1}&page_size={self.LIST_PAGE_SIZE}',
            None,
        )

    def open_request(self, ref: str) -> tuple[str, dict | None]:
        """browser/start with the start page suppressed (m4): the
        browser opens straight on the profile's platform URL."""
        return f'{self.P_START}?user_id={ref}&ip_tab=0&open_tabs=0', None

    def close_request(self, ref: str) -> tuple[str, dict | None]:
        return f'{self.P_STOP}?user_id={ref}', None

    def next_page(self, payload, page: int) -> int | None:
        """Continue while the last page was full (a short page ends)."""
        rows = ((payload or {}).get('data') or {}).get('list') or []
        if len(rows) >= self.LIST_PAGE_SIZE:
            return page + 1
        return None

    async def discover_port(self) -> int | None:
        """The port from the client's ``local_api`` file (a full URL).

        The file can be STALE (a killed client leaves it behind), so
        callers decide liveness by probing, never by this file alone.
        Falls back to the documented default when nothing is readable.
        """
        port = await asyncio.to_thread(self._read_port)
        return port if port else self.api_port

    def parse_stores(self, payload) -> list[StoreRef]:
        """user/list's ``data`` → StoreRefs.

        ``name`` is the ``user_id`` (the stable open handle — the
        contract suite and the picker's bind value open by it) with the
        human display (``#<serial> <name> (<group>)``) in
        ``remote_env`` and the platform URL in ``platform``. Every
        profile is ``available=True``: the list carries NO kernel field
        (verified live), so a Firefox-kernel profile cannot be greyed
        out here — it is refused at open with ``UnsupportedKernel``.
        """
        rows = ((payload or {}).get('data') or {}).get('list') or []
        out: list[StoreRef] = []
        for e in rows:
            user_id = str(e.get('user_id') or '')
            display = str(e.get('name') or user_id)
            serial = str(e.get('serial_number') or '')
            if serial:
                display = f'#{serial} {display}'
            group = str(e.get('group_name') or '')
            if group:
                display = f'{display} ({group})'
            out.append(
                StoreRef(
                    name=user_id,
                    platform=str(e.get('domain_name') or ''),
                    remote_env=display,
                    available=True,
                )
            )
        return out

    def parse_opened(self, payload) -> OpenedStore | None:
        """start's ``data`` → the kernel's CDP endpoint, or None.

        ``None`` (code 0 but empty ``ws``/``debug_port``) is ambiguous
        by design of the vendor: a Firefox kernel AND an orphaned
        SunBrowser both answer that way — :meth:`open_store` decides
        which it was from process evidence.
        """
        data = (payload or {}).get('data') or {}
        ws = data.get('ws') or {}
        host_port = str(ws.get('selenium') or '')
        parsed = urlsplit(f'//{host_port}') if host_port else None
        port = None
        host = '127.0.0.1'
        if parsed and parsed.port:
            port = parsed.port
            host = parsed.hostname or '127.0.0.1'
        else:
            try:
                port = int(str(data.get('debug_port') or ''))
            except ValueError:
                port = None
        if not port:
            return None
        return OpenedStore(host, port)

    @staticmethod
    def start_names_firefox(payload) -> bool:
        """Whether a code-0 start response names a FlowerBrowser kernel.

        The webdriver path carries the kernel (both shapes recorded
        live): ``…\\chrome_<N>\\chromedriver.exe`` is SunBrowser;
        ``…\\webdriver_flower_<N>\\driver.exe`` (flower_100) and
        ``…\\flower_<N>\\geckodriver.exe`` (flower_159, today's
        default) are FlowerBrowser — whose BiDi endpoint /json/version
        404s (a generic CdpUnreachable). Decided here so the refusal
        is typed, before the engine fronts the endpoint.
        """
        data = (payload or {}).get('data') or {}
        webdriver = str(data.get('webdriver') or '')
        if not webdriver:
            return False
        # Normalize to forward slashes so both \ and / paths work.
        parts = [
            p
            for p in ntpath.splitdrive(webdriver)[1]
            .replace('\\', '/')
            .split('/')
            if p
        ]
        if not parts:
            return False
        # A directory component naming a flower kernel (any version):
        # flower_100, webdriver_flower_100, flower_159, …
        for part in parts[:-1]:
            if re.fullmatch(r'(webdriver_)?flower_\d+', part, re.IGNORECASE):
                return True
        # Or the driver file itself is a geckodriver.
        return parts[-1].lower().startswith('geckodriver')

    def is_logged_out(self, status: int, payload) -> bool:
        """True for the no-credential shape — AdsPower's "not
        authenticated" answer.

        AdsPower has no SESSION-expiry shape (verified live, FACTS.md
        §c: signed out, a still-valid API key keeps working — the node
        service holds the session). What it does answer when Vibe
        Seller has no key stored is ``Require api-key``: the API needs
        a login material the user has not provided, which is exactly
        the shared contract's LOGIN_REQUIRED (the base's own default
        treats "the API needs a login the user hasn't supplied" the
        same way). The generate-a-key advice lives in the
        ``vendors.adspower.LoginRequired`` i18n override.

        "The client is signed out entirely" (the API answers nothing)
        is still decided at the PROCESS level, in
        :meth:`ensure_running` — the ONE function that owns it (R3).
        """
        if self._auth_value:
            return False  # a key is stored; refusals carry real reasons
        return (
            status == 200
            and isinstance(payload, dict)
            and str(payload.get('msg') or '') == 'Require api-key'
        )

    def is_rate_limited(self, status: int, payload) -> bool:
        """AdsPower rate-limits with HTTP 200 + a code -1 body."""
        return (
            status == 200
            and isinstance(payload, dict)
            and str(payload.get('msg') or '').startswith(self.MSG_RATE_LIMIT)
        )

    def map_error(self, status: int, payload) -> InjectError | None:
        """A non-success vendor payload → the shared taxonomy, or None.

        The ONE place vendor responses map to taxonomy errors (R3).
        Product-specific fix hints live HERE (the vendor module), never
        in the shared taxonomy. Every message below was verified live.
        """
        if not isinstance(payload, dict) or payload.get('code') == 0:
            return None
        msg = str(payload.get('msg') or '')
        if msg == 'Require api-key':
            # A key IS stored and was sent (the keyless shape is
            # intercepted as logged-out before map_error): the API
            # does not recognize the session — sign-in needed (R16b).
            # 'Require api-key' is documented as the no-key-in-request
            # shape (FACTS §b); the PERMISSION refusal is unverified
            # live and stays unmapped rather than guessed.
            return LoginRequired(f'{SIGN_IN_NEEDED_ZH} / {SIGN_IN_NEEDED_EN}')
        if msg.startswith('API Key mismatch'):
            # R8 case C (known issue, not live-tested — no second
            # account): when the CLIENT is signed into a different
            # account behind our back, every call answers this, so it
            # surfaces as AccountMismatch and no store ever opens.
            return AccountMismatch(
                'AdsPower 不认这个 API Key：Key 要从 AdsPower 当前登录'
                '的账号里复制；换过账号或重置过 Key，都要重新填。若要换'
                '账号登录 AdsPower，请先删除所有 AdsPower 店铺。/ The '
                'key must come from the account currently signed into '
                'AdsPower; re-copy it after an account or key change. To '
                'switch accounts, delete every AdsPower store first.'
            )
        if msg == 'invalid disordered id':
            return StoreNotFound(
                'AdsPower 里找不到这个环境：可能被删了，或者没有授权给'
                '当前登录的账号。/ AdsPower does not know this profile: '
                'deleted, or not authorized for the signed-in account.'
            )
        if msg == 'User_id is not open':
            return None  # benign on stop (already closed)
        if msg.startswith(self.MSG_RATE_LIMIT):
            # Exhausted resends (another consumer on the same client):
            # transient — "grant API permission" advice would be wrong.
            # StoreUnavailable on both list and start (chosen because
            # it carries retry semantics; see REPORT.md).
            return StoreUnavailable(
                'AdsPower 接口正忙，请稍后再试。/ AdsPower is busy, '
                'retry shortly.'
            )
        # Kernel messages are start-path states, not taxonomy errors;
        # open_store's kernel paths own them (R11).
        return None

    # ── the shared response path, with AdsPower's two additions ─────

    async def _super_api_call(
        self, method: str, path: str, json_body: dict | None
    ) -> tuple[int, object]:
        """The base's path with the keyless advice attached (R16a).

        The base raises a BARE LoginRequired on the keyless
        not-authenticated shape (before map_error); the generate-key
        advice rides the message. A keyed LoginRequired (map_error's
        sign-in branch) never passes through here.
        """
        try:
            return await super().api_call(method, path, json_body)
        except LoginRequired:
            raise LoginRequired(f'{NO_KEY_ADVICE_ZH} / {NO_KEY_ADVICE_EN}')

    async def api_call(
        self, method: str, path: str, json_body: dict | None = None
    ) -> tuple[int, object]:
        """The base's shared path plus the bounded resend + re-discovery.

        - a rate-limited answer is RE-SENT up to RATE_LIMIT_RETRIES
          rounds (the base retries once per round; the vendor's burst
          window outlives a single retry when another consumer calls);
        - a transport failure re-reads the ``local_api`` file ONCE and
          retries: the port changes on every client launch, so a dead
          port may just be a stale file.
        """
        if self._port is None:
            self._port = await self.discover_port()
        last = None
        for attempt in range(self.RATE_LIMIT_RETRIES + 1):
            try:
                status, payload = await self._super_api_call(
                    method, path, json_body
                )
            except ClientNotRunning:
                if attempt > 0 or self._port_pinned:
                    raise
                # The port may have moved (a client restart): re-read
                # the file once and retry.
                self._port = await self.discover_port()
                status, payload = await self._super_api_call(
                    method, path, json_body
                )
            last = (status, payload)
            if not self.is_rate_limited(status, payload):
                return status, payload
            logger.info(
                'adspower rate-limited %s (round %d); backing off',
                path,
                attempt + 1,
            )
        return last

    # ── the InjectClient contract (vendor overrides) ────────────────

    async def ensure_running(self) -> None:
        """D4: probe → launch the GUI → grace → headless-with-key.

        (1) the API answers /status → done. (2) the GUI shell is not
        running → launch it (remember-me signs the account in) and wait
        for the API. (3) the shell runs but the API never came up —
        the m1 evidence of a signed-out GUI (the Local API only starts
        at login) — with a 30 s grace for a shell someone else just
        started, then a headless instance that logs in with the API key
        (the vendor's documented flags); the GUI is NOT killed (they
        coexist). (4) still nothing → LoginRequired when the shell is
        up, ClientNotRunning when it never started.
        """
        if await self._api_up():
            return
        if self._port_pinned:
            # An explicitly configured api_port pins the whole
            # discovery/launch machinery: probe THIS port and fail with
            # ClientNotRunning when it is down (the base's semantics —
            # the operator said where the API is, so "it moved" or
            # "launch it" are not ours to decide).
            raise ClientNotRunning(
                f'the AdsPower local API on port {self._port} is not answering'
            )
        launched = False
        if not await asyncio.to_thread(self._proc.shell_running):
            launched = await asyncio.to_thread(self._proc.launch)
            if not launched:
                raise ClientNotRunning(
                    self._proc.locate_error
                    or 'could not launch the AdsPower client'
                )
            if await self._wait_for_api(self.GUI_WAIT_S):
                return
        # The Local API never came up. If the shell runs, that MAY be
        # the signed-out state (m1) — but a shell someone else started
        # moments ago (the user, Windows autostart) is still signing in
        # (remember-me + node service take a while), so give it a
        # bounded grace before concluding (R3: no needless headless
        # instance, no second node service, no port-file churn).
        if self._auth_value and await asyncio.to_thread(
            self._proc.shell_running
        ):
            if not launched and await self._wait_for_api(
                self.SIGNED_OUT_GRACE_S
            ):
                return
            logger.info(
                'adspower GUI is up but its API is not (signed out?); '
                'starting a headless instance with the API key'
            )
            if await asyncio.to_thread(
                self._proc.launch_headless, self._auth_value
            ):
                if await self._wait_for_api(self.HEADLESS_WAIT_S):
                    return
        if launched and not await asyncio.to_thread(self._proc.shell_running):
            raise ClientNotRunning('the AdsPower client exited')
        # R16b: the same wording the store form shows (the constants
        # the vendors.adspower.LoginRequired override carries).
        raise LoginRequired(f'{SIGN_IN_NEEDED_ZH} / {SIGN_IN_NEEDED_EN}')

    async def _api_up(self) -> bool:
        """``GET /status`` answers (it needs no key)."""
        try:
            await self.api_call('GET', self.P_STATUS)
        except ClientNotRunning:
            return False
        except InjectError:
            return True  # the API answered — it is running
        return True

    async def _wait_for_api(self, budget_s: float) -> bool:
        """Poll /status until it answers, within a budget."""
        deadline = time.monotonic() + budget_s
        while time.monotonic() < deadline:
            await asyncio.sleep(self.LAUNCH_POLL_S)
            if await self._api_up():
                return True
        return False

    async def ensure_logged_in(self) -> None:
        """No-op: the login decision is process-level (see docstring).

        AdsPower has no logged-out API shape and no password-login
        endpoint — :meth:`ensure_running` is what handles "not signed
        in" (headless with the key), and a key that does not match the
        signed-in account surfaces as ``AccountMismatch`` on the first
        real call.
        """
        return None

    async def is_logged_in(self) -> bool:
        """True — see :meth:`ensure_logged_in` for why this cannot lie."""
        return True

    async def list_stores(self) -> list[StoreRef]:
        """Every profile, paged to the end (the base's pagination with
        the rate limit between pages)."""
        return await super().list_stores()

    async def open_store(self, ref: str) -> OpenedStore:
        """browser/start → the kernel's CDP endpoint.

        Start is idempotent (same endpoints when already open, by hand
        or by us), so attaching never reopens. A kernel still
        downloading is polled (R11: ``is updating`` full budget, ``is
        not ready`` brief + actionable hint). EVERY other failed start
        — the empty endpoint (code 0, no ``ws``) AND any unmapped
        ``code:-1`` — is decided from process evidence in
        :meth:`_decide_failed_start` (R1): a Firefox kernel and an
        orphaned SunBrowser can surface as either shape. Only auth /
        not-found errors bypass the decision.
        """
        path, body = self.open_request(ref)
        data = await self.api_call(self.open_method, path, json_body=body)
        data = await self._resolve_start(ref, path, data)
        _, payload = data
        if self.start_names_firefox(payload):
            # R14: the start response itself names a FlowerBrowser
            # kernel (webdriver_flower in the driver path) — refuse
            # BEFORE the engine ever fronts the BiDi endpoint (which
            # /json/version 404s and would surface as a generic
            # CdpUnreachable). _refuse_firefox stops the profile and
            # raises UnsupportedKernel with the plain-language hint.
            await self._refuse_firefox(ref)
        opened = self.parse_opened(payload)
        if opened is not None:
            # The ENGINE verifies the kernel positively (contract P9,
            # the backstop for unknown future shapes): a Firefox kernel
            # can answer a real-looking partial CDP (Firefox's old
            # Remote Agent), and the engine refuses it from
            # /json/version's Browser field. Our close_store then
            # cleans the window up (the API cannot stop a
            # FlowerBrowser).
            return opened
        return await self._decide_failed_start(ref, data)

    async def _resolve_start(
        self, ref: str, path: str, data: tuple[int, object]
    ) -> tuple[int, object]:
        """Apply the start-error policy, returning the final response.

        Auth / not-found errors raise here (they mean what they say);
        a kernel download is polled; everything else is handed to the
        caller as a failed response for :meth:`_decide_failed_start`.
        """
        for _ in range(2):
            status, payload = data
            if isinstance(payload, dict) and payload.get('code') == 0:
                return data
            err = self.map_error(status, payload)
            if isinstance(
                err,
                ApiNotEnabled | AccountMismatch | StoreNotFound | LoginRequired,
            ):
                raise err
            msg = str(
                (payload or {}).get('msg') if isinstance(payload, dict) else ''
            )
            if self.MSG_KERNEL_UPDATING in msg:
                # A download IS running (verified on Windows GUI:
                # ready in ~40 s) — poll the full budget, carrying the
                # sighting so a later 'is not ready' cannot flip the
                # hint (the GUI shape is updating → not ready → ready).
                return await self._await_kernel_download(
                    path, saw_updating=True
                )
            if self.MSG_KERNEL_NOT_READY in msg:
                # The kernel is MISSING and nothing downloads it from a
                # background session (verified live on macOS, R11) —
                # poll briefly in case a download starts, then fail
                # with the actionable hint.
                return await self._await_kernel_download(
                    path,
                    budget_s=self.KERNEL_NOT_READY_WAIT_S,
                )
            return data
        return data

    async def _await_kernel_download(
        self,
        path: str,
        budget_s: float | None = None,
        saw_updating: bool = False,
    ) -> tuple[int, object]:
        """Poll start while the kernel is unavailable (bounded, logged).

        ``is updating`` gets the full budget (a download is running);
        ``is not ready`` gets a short one (nothing downloads it from a
        background session — verified live on macOS, R11). The timeout
        error names which shape it was, so the hint matches reality.
        """
        budget = self.KERNEL_TIMEOUT_S if budget_s is None else budget_s
        deadline = time.monotonic() + budget
        last_msg = ''
        # The Windows GUI shape is updating → not ready → ready: once a
        # download has been SEEN (here or by the caller), a timeout
        # means it is slow, not absent — the hint stays
        # 'downloading, retry' (R11b).
        while time.monotonic() < deadline:
            await asyncio.sleep(self.KERNEL_POLL_S)
            status, payload = await self.api_call(self.open_method, path)
            if not isinstance(payload, dict) or payload.get('code') == 0:
                return status, payload
            last_msg = str(payload.get('msg') or '')
            if self.MSG_KERNEL_UPDATING in last_msg:
                saw_updating = True
            if (
                self.MSG_KERNEL_UPDATING not in last_msg
                and self.MSG_KERNEL_NOT_READY not in last_msg
            ):
                return status, payload
            logger.info('adspower kernel still unavailable: %s', last_msg)
        if self.MSG_KERNEL_NOT_READY in last_msg and not saw_updating:
            # Kernel name+version from the vendor message
            # ("SunBrowser 156 is not ready,please to download!").
            kernel = last_msg.split(self.MSG_KERNEL_NOT_READY)[0].strip()
            raise StoreUnavailable(
                f'AdsPower 还没有这个环境要用的浏览器内核（{kernel}）：'
                '后台不会自动下载。请打开 AdsPower 客户端，在设置里下载该'
                '内核（或把环境改成已安装的内核版本）后重试。/ AdsPower '
                f"does not have this profile's browser kernel ({kernel}) "
                'yet, and it does NOT download by itself in the '
                'background. Open the AdsPower app, download that kernel '
                'in its settings (or switch the profile to an installed '
                'version), then retry.'
            )
        raise StoreUnavailable(
            'AdsPower 正在下载浏览器内核，下载完再试一次。'
            f'({last_msg or "download timed out"})'
        )

    async def _decide_failed_start(
        self, ref: str, data: tuple[int, object]
    ) -> OpenedStore:
        """A failed start, decided by process evidence (D7, R1).

        Verified live: a FlowerBrowser (Firefox) profile can surface as
        EITHER shape — code 0 with empty ``ws`` fields, or
        ``code:-1 "Browser execution startup timeout"`` — and the
        window still opens in both; an orphaned SunBrowser (left by a
        killed client) answers the empty shape. The list carries no
        kernel field (m5), so only the kernel processes can tell them
        apart. Both kernels name the profile in
        ``--user-data-dir=<cache>/<profile_id>_<inviteCode>`` (m6), so
        exactly THIS profile's tree is found and killed — never a
        whole image name (other profiles may belong to other tasks or
        to the user).
        """
        kernels = await asyncio.to_thread(self._list_kernels)
        mine = self._kernels_for(kernels, str(ref))
        for proc in mine:
            if proc.is_firefox:
                # We opened it and the API cannot stop it (FACTS §j):
                # clean up our own window, then refuse with the hint.
                await self._refuse_firefox(ref)
        retry_data = data
        for proc in mine:  # a SunBrowser orphan from a killed client
            logger.warning(
                'adspower profile %s is locked by an orphaned kernel '
                '(pid %s); killing that tree and starting fresh',
                ref,
                proc.pid,
            )
            await asyncio.to_thread(self._kill_kernel, proc)
            await asyncio.sleep(2.0)
            path, body = self.open_request(ref)
            retry_data = await self.api_call(
                self.open_method, path, json_body=body
            )
            _, retry_payload = retry_data
            if isinstance(retry_payload, dict) and (
                retry_payload.get('code') == 0
            ):
                opened = self.parse_opened(retry_payload)
                if opened is not None:
                    return opened
        # Neither kernel (or the orphan retry failed): the error for
        # the shape we saw — an empty endpoint means "no CDP came
        # back", a code -1 carries the vendor's own message (R1).
        status, payload = retry_data
        if isinstance(payload, dict) and payload.get('code') != 0:
            msg = str(payload.get('msg') or 'unknown error')
            raise StoreUnavailable(f'AdsPower: {msg}')
        raise CdpUnreachable(
            'AdsPower returned no debug endpoint for this profile ('
            f'{(payload or {}).get("msg") if isinstance(payload, dict) else "empty ws"})'
        )

    async def _refuse_firefox(self, ref: str) -> None:
        """Refuse the profile; :meth:`close_store` cleans the window.

        close_store is the ONE cleanup owner: a refused stop plus a
        live flower kernel for this profile kills that tree (the API
        cannot stop a FlowerBrowser).
        """
        await self.close_store(ref)
        raise UnsupportedKernel(UNSUPPORTED_KERNEL_TEXT)

    @staticmethod
    def _kernels_for(
        kernels: list[KernelProcess], ref: str
    ) -> list[KernelProcess]:
        """The kernel processes whose user-data-dir names ``ref``."""
        prefix = f'{ref}_'
        out = []
        for k in kernels:
            segment = k.user_data_dir.replace('/', '\\').rstrip('\\')
            if segment.rsplit('\\', 1)[-1].startswith(prefix):
                out.append(k)
        return out

    def _kill_kernel(self, proc: KernelProcess) -> None:
        """Kill one profile's kernel tree (best effort, logged)."""
        if not self._kill_tree(proc.pid):
            logger.warning(
                'could not kill kernel pid %s for profile cleanup',
                proc.pid,
            )

    async def close_store(self, ref: str) -> None:
        """Best-effort browser/stop + Firefox-window cleanup.

        Stop is cleanup, so every refusal is only logged — including
        ``User_id is not open``. But a FlowerBrowser window the API
        CANNOT stop (FACTS §j: local-active does not track it) would
        linger after the engine's kernel refusal closed the session,
        so a refused stop with a live flower kernel for THIS profile
        kills that tree — we opened it, and nothing else can close it.
        """
        refused = False
        try:
            path, body = self.close_request(ref)
            status, payload = await self.api_call(
                self.close_method, path, json_body=body
            )
            if isinstance(payload, dict) and payload.get('code') != 0:
                refused = True
                logger.info(
                    'adspower stop for %r said: %s',
                    ref,
                    payload.get('msg'),
                )
        except Exception as e:  # never raise from cleanup
            logger.warning('adspower stop for %r failed: %s', ref, e)
        if refused:
            kernels = await asyncio.to_thread(self._list_kernels)
            for proc in self._kernels_for(kernels, str(ref)):
                if proc.is_firefox:
                    await asyncio.to_thread(self._kill_kernel, proc)
