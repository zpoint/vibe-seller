"""Real Wenmai (稳卖) client: local HTTP API + inspector login + CDP.

LocalApiClient-shaped (ARCHITECTURE.md §3): list / open / close talk to
the client's own local HTTP API (Express, port 3980) with the vendor's
rate limit, and the response handling lives in small overridable
methods (``parse_stores`` / ``parse_opened`` / ``is_logged_out`` /
``map_error``). The class stays local to this adapter until the
refactor's shared ``LocalApiClient`` base lands; moving it up is a
mechanical follow-up.

Two things still need more than plain HTTP (R2 — the API cannot do
them):

- ``ensure_logged_in``: the official API has no password login (the
  cloud login response is encrypted, and ``uploadToken`` needs a token
  we cannot obtain cleanly), so auto-login drives the client's own
  login page in its renderer through the shared Node inspector
  (:mod:`app.browser.win_inspector`) — the same technique as 悦互联.
- ``after_open``: a fresh env opens on a store-entry middle page whose
  打开店铺 button must be clicked over CDP before the agent starts.

``open_store`` returns the kernel's OWN debug endpoint (the ``selenium``
field): every Chromium kernel launches with ``--remote-debugging-port``,
including stores a person opened by hand, and opening an already-open
store returns the SAME pid and port — so no port injection and no
close/reopen are ever needed (unlike 悦互联 / Ziniao).

API shapes verified live 2026-11-27 (client v5.1.0715). The official
docs are unreliable (they say 401 for logged-out; the real shapes are
in :meth:`is_logged_out`) and rate-limit to one request per 0.5 s, so
every call is serialised through one per-loop gate.

Credential schema (arrives in ``browser_config`` under neutral keys,
matching the future generic credential storage): ``company``
(企业名称), ``username`` (企业用户名), ``password`` (企业密码) —
enterprise login only; the phone tab has an image captcha. One account
per machine. Until the registry lands, a caller (the live smoke / a
test server's store config) passes them straight into the config.
"""

import asyncio
from contextlib import asynccontextmanager
import logging
import time
from urllib.parse import urlparse
import weakref

import aiohttp

from app.browser.adapters.wenmai_dom import (
    CLICK_OPEN_STORE_JS,
    LOGIN_HASH_JS,
    fill_enterprise_login_js,
    is_logged_in_hash,
)
from app.browser.adapters.wenmai_win import (
    DEFAULT_API_PORT,
    WenmaiProcessLayer,
)
from app.browser.inject.client import InjectClient, OpenedStore, StoreRef
from app.browser.inject.errors import (
    AccountMismatch,
    CdpUnreachable,
    ClientNotRunning,
    InjectError,
    LoginRequired,
    StoreNotFound,
    StoreUnavailable,
    UnsupportedKernel,
)
from app.browser.win_inspector import InspectorSession

logger = logging.getLogger(__name__)

_LOCALHOST = '127.0.0.1'

# One request at a time, >= 0.5 s apart (the documented rate limit),
# shared by every client instance on the loop — store setup's list and
# a task's open talk to the same client.
_API_GATES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
# A separate gate for driving the client's GUI (the inspector): a login
# polls for up to 30 s and must not hold the API gate hostage.
_GUI_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _api_gate() -> list:
    """The per-loop [lock, last-call ts] serialising client API calls."""
    loop = asyncio.get_running_loop()
    gate = _API_GATES.get(loop)
    if gate is None:
        gate = _API_GATES[loop] = [asyncio.Lock(), 0.0]
    return gate


def _gui_lock() -> asyncio.Lock:
    """The per-loop lock serialising drives of the client's one window."""
    loop = asyncio.get_running_loop()
    lock = _GUI_LOCKS.get(loop)
    if lock is None:
        lock = _GUI_LOCKS[loop] = asyncio.Lock()
    return lock


class RealWenmaiClient(InjectClient):
    """Drives the real 稳卖 Electron client on Windows via its API."""

    # ── vendor constants (class attributes; the LocalApiClient shape) ──
    #: The client's local API port (fixed; verified live).
    API_PORT = DEFAULT_API_PORT
    #: Documented rate limit: one request per 0.5 s.
    MIN_CALL_GAP_S = 0.55
    #: Relay readiness budget: the store methods are relayed by the
    #: shell to the main window's web page, so a freshly launched
    #: client answers nothing until that page has loaded.
    RELAY_TIMEOUT_S = 120.0
    RELAY_POLL_S = 2.0
    #: One relayed call may hang while the window is still loading.
    CALL_TIMEOUT_S = 20.0
    #: queryEnvV2's documented maximum page size.
    LIST_PAGE_SIZE = 100

    # API method names (POST JSON with a ``method`` field).
    M_LIST = 'queryEnvV2'
    M_OPEN = 'openEnv'
    M_CLOSE = 'closeEnv'
    M_USER_INFO = 'getUserInfo'
    #: Shell-answered GET (readiness probe that needs no relay).
    M_SHELL_PING = 'getAllInfo'

    def __init__(self, browser_config: dict | None = None):
        cfg = browser_config or {}
        self._proc = WenmaiProcessLayer(cfg.get('client_path') or None)
        # Enterprise-login credentials (neutral schema — module
        # docstring). Empty → no auto-login.
        self._company = cfg.get('company') or ''
        self._username = cfg.get('username') or ''
        self._password = cfg.get('password') or ''
        self._base = f'http://{_LOCALHOST}:{self.API_PORT}'

    # ── HTTP layer ──────────────────────────────────────────────────

    async def _request(
        self, method: str, payload: dict | None = None
    ) -> dict | None:
        """One serialised, rate-limited API call; None when unreachable.

        GET for the shell-answered methods, POST JSON with a ``method``
        field for the relayed ones (the shapes verified live).
        """
        gate = _api_gate()
        async with gate[0]:
            gap = self.MIN_CALL_GAP_S - (time.monotonic() - gate[1])
            if gap > 0:
                await asyncio.sleep(gap)
            try:
                async with aiohttp.ClientSession() as s:
                    if payload is None:
                        async with s.get(
                            f'{self._base}/{method}',
                            timeout=aiohttp.ClientTimeout(
                                total=self.CALL_TIMEOUT_S
                            ),
                        ) as r:
                            body = await r.json(content_type=None)
                    else:
                        async with s.post(
                            f'{self._base}/{method}',
                            json=payload,
                            timeout=aiohttp.ClientTimeout(
                                total=self.CALL_TIMEOUT_S
                            ),
                        ) as r:
                            body = await r.json(content_type=None)
                gate[1] = time.monotonic()
                return body if isinstance(body, dict) else None
            except (TimeoutError, aiohttp.ClientError, ValueError):
                gate[1] = time.monotonic()
                return None

    async def _call(self, method: str, payload: dict | None = None):
        """A relayed POST (``method`` in the body)."""
        body = dict(payload or {})
        body['method'] = method
        return await self._request(method, body)

    # ── response parsing (small overridable methods) ────────────────

    def is_logged_out(self, payload: dict | None) -> bool:
        """True when an API payload says 'not logged in' (either shape).

        The ONE place logged-out is decided (R3). The docs claim 401;
        the client (v5.1.0715) actually answers, verified live:

        - relayed store methods (queryEnvV2 / openEnv / …):
          ``{"method": "...", "msg": "认证失败，无法访问系统资源",
          "status": "error", "code": "400"}`` — at the TOP level;
        - ``getUserInfo``: ``{"response": {"code": 200, "msg": "认证失败…",
          "token": null}}`` — code 200 still means logged out there; the
          tell is ``token`` being null.
        """
        if not isinstance(payload, dict):
            return False
        resp = payload.get('response')
        if isinstance(resp, dict) and 'token' in resp:
            return resp.get('token') is None
        msg = str(payload.get('msg') or '')
        return payload.get('status') == 'error' and '认证失败' in msg

    def parse_stores(self, resp: dict) -> list[StoreRef]:
        """queryEnvV2's ``response`` → StoreRefs.

        Carries the device-bound state: an env with no device bound
        (``ipId`` null) cannot be opened, so it is returned with
        ``available=False`` for the UI to grey out.
        """
        envs = resp.get('envs') or []
        return [
            StoreRef(
                name=str(e.get('storeName') or e.get('name') or e['id']),
                platform=str(
                    e.get('platform') or e.get('actualPlatform') or ''
                ),
                ref=str(e['id']),
                available=e.get('ipId') is not None,
            )
            for e in envs
        ]

    def parse_opened(self, resp: dict) -> OpenedStore:
        """openEnv's ``response`` → the kernel's own CDP endpoint.

        A Firefox-kernel env gets ``webdriverPath: null`` and no debug
        port → UnsupportedKernel. The ``selenium`` field IS the CDP
        endpoint (its /json/version serves webSocketDebuggerUrl).
        """
        if not resp.get('webdriverPath'):
            raise UnsupportedKernel(
                'this store runs a non-Chromium (Firefox) kernel; only '
                'Chromium-kernel stores are supported (在稳卖客户端把 '
                '这个店铺切换为 Chromium 内核)'
            )
        parsed = urlparse(str(resp.get('selenium') or ''))
        if not parsed.port:
            raise CdpUnreachable(f'openEnv returned no CDP endpoint: {resp}')
        return OpenedStore(parsed.hostname or _LOCALHOST, parsed.port)

    def map_error(self, payload: dict) -> InjectError | None:
        """A non-success vendor payload → the shared taxonomy, or None.

        The ONE place vendor responses map to taxonomy errors (R3).
        Product-specific fix hints live HERE (the vendor module), never
        in the shared taxonomy.
        """
        if self.is_logged_out(payload):
            return LoginRequired()
        resp = payload.get('response')
        msg = str((resp or {}).get('msg') or payload.get('msg') or 'unknown')
        if '未绑定' in msg and '设备' in msg:
            return StoreUnavailable(f'{msg} — 在稳卖客户端给这个店铺绑定设备')
        return CdpUnreachable(f'the 稳卖 client refused the call: {msg}')

    # ── InjectClient ────────────────────────────────────────────────

    async def ensure_running(self) -> None:
        """Up = the API answers AND relays to the main window.

        The store methods are relayed over a WebSocket to the main
        window's web page, so a launched-but-still-loading client hangs
        them. Readiness is therefore queryEnvV2 answering at all (the
        logged-out shape counts — that IS an answer), not the shell's
        own endpoints alone.
        """
        if await self._relay_answers():
            return
        # The shell may be up but its window still loading (a normal
        # launch the API hasn't caught up with): only a dead API means
        # the client is not running and needs a launch.
        if await self._request(self.M_SHELL_PING) is None:
            if not await asyncio.to_thread(self._proc.launch):
                detail = (
                    self._proc.locate_error
                    or 'could not launch the WenMaiAIBrowser client'
                )
                raise ClientNotRunning(detail)
        deadline = time.monotonic() + self.RELAY_TIMEOUT_S
        while time.monotonic() < deadline:
            if await self._relay_answers():
                return
            await asyncio.sleep(self.RELAY_POLL_S)
        raise ClientNotRunning(
            'client is running but its API never answered '
            '(the main window did not load); retry, or open the 稳卖 '
            'client once by hand'
        )

    async def _relay_answers(self) -> bool:
        data = await self._call(self.M_LIST, {'pageNum': '1', 'pageSize': '1'})
        return data is not None

    async def is_logged_in(self) -> bool:
        info = await self._user_info()
        return bool(info and info.get('token'))

    async def ensure_logged_in(self) -> None:
        """Auto-login via 企业登录 when the client is logged out.

        No-op if already logged in (plus the account check: the client
        must be logged into the account whose credentials are saved —
        never log someone out automatically). With stored credentials,
        fill the enterprise form through the inspector and poll; without
        them, raise ``LoginRequired``. The password is only ever in the
        one eval, never logged.
        """
        info = await self._user_info()
        if info is None:
            raise ClientNotRunning('the 稳卖 client API is not answering')
        if info.get('token'):
            current = str(info.get('companyName') or '').strip()
            if self._company and current and current != self._company.strip():
                raise AccountMismatch(
                    'the 稳卖 client is logged into a different account '
                    f'(current: {current}); expected {self._company.strip()}'
                )
            return
        if not (self._company and self._username and self._password):
            raise LoginRequired()
        async with self._inspector() as insp:
            result = str(
                await insp.eval_in_renderer(
                    fill_enterprise_login_js(
                        self._company, self._username, self._password
                    )
                )
            )
            if result != 'OK':
                raise LoginRequired(
                    f'enterprise-login form could not be driven: {result}'
                )
            for _ in range(30):
                await asyncio.sleep(1.0)
                route = await insp.eval_in_renderer(LOGIN_HASH_JS)
                if is_logged_in_hash(str(route or '')):
                    return
            raise LoginRequired(
                'enterprise login submitted but the client stayed '
                'logged out (wrong credentials?)'
            )

    async def _user_info(self) -> dict | None:
        """getUserInfo's response dict (token NEVER logged)."""
        data = await self._call(self.M_USER_INFO)
        if data is None:
            return None
        if self.is_logged_out(data):
            return {}
        resp = data.get('response')
        return resp if isinstance(resp, dict) else {}

    @asynccontextmanager
    async def _inspector(self):
        # The inspector drives the client's only window: one driver at a
        # time, per loop (store setup's login and a task never click in
        # the same window at once).
        async with _gui_lock():
            pid = await asyncio.to_thread(self._proc.main_pid)
            if not pid:
                raise ClientNotRunning(
                    'no 稳卖 main process found (API port has no listener)'
                )
            insp = InspectorSession(self._proc.exe_path_script)
            await insp.open(pid)
            try:
                yield insp
            finally:
                await insp.close()

    async def list_stores(self) -> list[StoreRef]:
        """Page queryEnvV2 (pageSize 100) until ``total`` stores are read."""
        envs: list[StoreRef] = []
        total: int | None = None
        page = 1
        while True:
            data = await self._call(
                self.M_LIST,
                {'pageNum': str(page), 'pageSize': str(self.LIST_PAGE_SIZE)},
            )
            if data is None:
                raise ClientNotRunning('the 稳卖 client API is not answering')
            if self.is_logged_out(data):
                raise LoginRequired()
            resp = data.get('response') or {}
            batch = self.parse_stores(resp)
            envs.extend(batch)
            try:
                total = int(resp.get('total'))
            except (TypeError, ValueError):
                total = None
            if not batch or (total is not None and len(envs) >= total):
                break
            page += 1
        return envs

    async def open_store(self, ref: str) -> OpenedStore:
        """``openEnv`` → the kernel's own CDP endpoint.

        Typed errors via :meth:`map_error`; an id no login can see is
        StoreNotFound (checked against the list, because a wrong id and
        a transient failure must not read alike). Opening an already-
        open store returns the SAME pid and port, so a store a person
        opened by hand attaches without reopening.
        """
        data = await self._call(
            self.M_OPEN, {'id': str(ref), 'option': 'default'}
        )
        if data is None:
            raise ClientNotRunning('the 稳卖 client API is not answering')
        resp = data.get('response')
        if not isinstance(resp, dict) or resp.get('code') != 200:
            err = self.map_error(data)
            if err is not None:
                # A wrong id and a transient failure must not read
                # alike: confirm the env exists for this login first.
                if not await self._knows_env(ref):
                    raise StoreNotFound(ref) from err
                raise err
        return self.parse_opened(resp)

    async def _knows_env(self, ref: str) -> bool:
        """Whether ``ref`` (env id) is visible to the current login."""
        try:
            return any(s.ref == str(ref) for s in await self.list_stores())
        except Exception:  # unreadable list → don't mask the real error
            return True

    async def after_open(self, opened: OpenedStore) -> None:
        """Click 打开店铺 on the store-entry middle page.

        A fresh env opens on ``chrome-extension://<id>/middlePage/...``
        (a 店铺信息 card); the store's real page only appears after its
        打开店铺 button is clicked — or by itself when 以后自动打开 was
        ticked. 悦互联 lands directly on the store, so the agent must
        not have to discover this step: click it here, over CDP on the
        middle-page target (matched by PATH, never by extension id),
        then wait for the platform page target. The engine calls this
        BEFORE the CDPMuxProxy fronts the env, and the proxy's
        keep_last_page keeps the produced page alive.
        """
        pages = await self._cdp_pages(opened)
        if self._store_page(pages):
            return  # auto-open, or a person opened the store by hand
        middle = self._middle_page(pages)
        if middle is None:
            # Nothing to click and no store page: the env may open
            # straight to the store in a future client. Loud failure is
            # wrong here (the env IS open and CDP answers) — log and let
            # the agent navigate.
            logger.warning(
                '稳卖 env on %s:%s has no middlePage and no store page; '
                'continuing without clicking 打开店铺',
                opened.cdp_host,
                opened.cdp_port,
            )
            return
        clicked = await self._cdp_eval(middle, CLICK_OPEN_STORE_JS)
        if clicked != 'OK':
            # The button may be gone because auto-open already fired;
            # give the page a moment before failing.
            logger.info(
                'middlePage had no 打开店铺 button (%s); waiting for the '
                'store page anyway',
                clicked,
            )
        for _ in range(15):
            await asyncio.sleep(2.0)
            pages = await self._cdp_pages(opened)
            if self._store_page(pages):
                return
        raise CdpUnreachable(
            'clicked 打开店铺 on the middle page but no store page opened '
            f'({opened.cdp_host}:{opened.cdp_port})'
        )

    @staticmethod
    def _store_page(pages: list[dict]) -> dict | None:
        """The first real web page target (the store's platform page)."""
        for p in pages:
            if str(p.get('url', '')).startswith(('http://', 'https://')):
                return p
        return None

    @staticmethod
    def _middle_page(pages: list[dict]) -> dict | None:
        """The store-entry middle page target (matched by path)."""
        for p in pages:
            if 'middlepage' in str(p.get('url', '')).lower():
                return p
        return None

    async def _cdp_pages(self, opened: OpenedStore) -> list[dict]:
        """Page targets from the kernel's CDP ``/json/list``."""
        url = f'http://{opened.cdp_host}:{opened.cdp_port}/json/list'
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as r:
                    targets = await r.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, ValueError):
            return []
        if not isinstance(targets, list):
            return []
        return [t for t in targets if t.get('type') == 'page']

    async def _cdp_eval(self, target: dict, js: str):
        """Runtime.evaluate ``js`` on one page target's WS endpoint."""
        ws_url = target.get('webSocketDebuggerUrl')
        if not ws_url:
            return 'NOWS'
        try:
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect(ws_url, max_msg_size=0) as ws:
                    await ws.send_json({
                        'id': 1,
                        'method': 'Runtime.evaluate',
                        'params': {
                            'expression': js,
                            'returnByValue': True,
                            'awaitPromise': True,
                        },
                    })
                    while True:
                        msg = await asyncio.wait_for(
                            ws.receive_json(), timeout=10
                        )
                        if msg.get('id') == 1:
                            res = msg.get('result', {}).get('result', {})
                            return res.get('value')
        except Exception as e:  # best-effort click; the wait loop decides
            logger.warning('middlePage CDP eval failed: %s', e)
            return 'EVALFAIL'

    async def close_store(self, ref: str) -> None:
        """Best-effort closeEnv (the engine already sent Browser.close)."""
        try:
            await self._call(self.M_CLOSE, {'id': str(ref)})
        except Exception as e:  # never raise from cleanup
            logger.warning('closeEnv for %r failed: %s', ref, e)
