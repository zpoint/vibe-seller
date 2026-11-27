"""BitBrowser (比特浏览器) — a pure local-API anti-detect browser backend.

BitBrowser's Windows client serves a local HTTP API (Fastify inside the
Electron main process, default port 54345) with list / open(id) → CDP /
close, so this vendor is a :class:`~app.browser.local_api_client
.LocalApiClient` subclass — no GUI injection and **no Node inspector**
for any flow (R2):

- The client remembers its login across a kill and relaunch, and the
  login form lives in a REMOTE web app (``client.bitbrowser.cn``) the
  vendor can change at any time. Automating it would add inspector-port
  contention and a stored password for no gain, so no credentials are
  stored at all (one machine login, nothing persisted): the user signs in
  once in BitBrowser and the local API acts as that login.
  ``LOGIN_REQUIRED`` is therefore a clear, actionable error — open
  比特浏览器 and sign in; it stays signed in.

Facts pinned by the Stage-1 probe (``~/bitbrowser-glm/FACTS.md`` on the
dev machine; the live shapes are mirrored in this module's tests):

- Port and the optional API token live in the client's own
  ``%APPDATA%\\BitBrowser\\config.json`` (``localServerAddress`` /
  ``enableAuth`` + ``authToken``) — read from there, never stored by us.
- Every call is POST + JSON. Business failures answer HTTP 200 with
  ``{"success": false, "msg": ...}``; a wrong/missing ``x-api-key``
  answers HTTP 403; the rate limit (10 req/s) answers HTTP 429.
- ``/browser/list`` rows carry each profile's SITE credentials
  (userName / password / cookie / proxy passwords), so
  :meth:`BitBrowserClient.parse_stores` keeps an allow-list and the
  base never logs payloads.
- Opening a profile launches a 工作台 (workbench) tab at
  ``console.bitbrowser.net`` next to the platform page.
  :meth:`BitBrowserClient.after_open` closes the workbench tab over
  CDP (only while another page tab exists — closing the last tab would
  close the browser) and activates the store tab, so the agent lands
  on the store, never on the workbench.
- ``coreProduct`` on every list row and in ``/browser/detail`` says
  which kernel a profile uses. Firefox-kernel profiles have no CDP, so
  they are greyed out in the picker and refused at open with
  ``UNSUPPORTED_KERNEL``.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.browser.adapters import bitbrowser_login as login
from app.browser.adapters.bitbrowser_cdp import (
    WORKBENCH_HOST as _WORKBENCH_HOST,
    activate_target as _activate_target,
    close_target as _close_target,
    is_http_page as _is_http_page,
    page_targets as _cdp_page_targets,
    platform_hostname,
    preferred_tab as _preferred,
)
from app.browser.adapters.bitbrowser_launch import (
    BITBROWSER_WIN_APP,
    _mac_locator,
    launch_exe as _launch_exe,
    mac_app_descriptor,
    port_from_address,
    read_client_config,
)
from app.browser.base import BrowserSessionInfo
from app.browser.inject.client import (
    UNAVAILABLE_UNSUPPORTED_KERNEL,
    OpenedStore,
    StoreRef,
)
from app.browser.inject.engine import ClientFactory, InjectCdpBackend
from app.browser.inject.errors import (
    ApiNotEnabled,
    CdpUnreachable,
    ClientNotRunning,
    InjectError,
    LoginRequired,
    StoreNotFound,
    StoreUnavailable,
    UnsupportedKernel,
    UnsupportedPlatform,
)
from app.browser.local_api_client import LocalApiClient, opened_from_ws
from app.browser.vendors import (
    CARDINALITY_SINGLE,
    CLIENT_LOCAL_API,
    KIND_ANTIDETECT,
    PICKER_GENERIC,
    PROFILES_LOGIN,
    BrowserVendorSpec,
    VendorCredentialField,
    VendorDocsLink,
)
from app.browser.win_app_locator import locate as locate_win_app
from app.browser.ziniao_utils import get_platform
from app.config import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

# The launch poll: after starting the exe, /health is asked this often
# for this long (the client binds its port in ~1 s; 30 s is generous
# for a cold start with a kernel download).
_LAUNCH_POLL_ATTEMPTS = 15
_LAUNCH_POLL_DELAY_S = 2.0

_SUPPORTED_PLATFORMS = frozenset({"windows", "wsl", "mac"})

# The logged-out bodies. TWO shapes, both HTTP 200 + success:false:
# 1. {"success":false,"msg":"token 失效，请检查登录状态"} — the session
#    token expired / the user logged out in the client. A hardcoded
#    literal in the client's cloud-request layer (electron-main.js, the
#    only 「请检查登录状态」 occurrence); no English variant of that
#    response and no locale-independent code field, so the zh text is
#    the signal ('token is invalid' matches the client's own English
#    LOG line, kept in case a build localizes it).
# 2. {"success":false,"msg":"Login out！"} — a DEVICE KICK: one
#    BitBrowser account is signed in on one computer at a time, and
#    signing in elsewhere kicks this client out (recorded verbatim on
#    a live kick; English message, FULLWIDTH !).
# Only these marks — never a blanket success:false, which also carries
# genuine store errors.
_TOKEN_EXPIRED_MARKS = (
    'token 失效',
    'token is invalid',
    'Login out！',
)


# Auto-login hints (zh, per the module's message convention).
_KICK_HINT = (
    '这个比特浏览器账号在另一台电脑登录了，本机已退出。请在比特浏览器里重新'
    '登录（或确认只在一台电脑上使用）'
)
_NO_CREDENTIALS_HINT = (
    '比特浏览器未登录且没有保存的账号密码。请在设置里填写比特浏览器账号和'
    '密码，或在比特浏览器里手动登录'
)


def _form_failure_hint(code: str) -> str:
    if code == 'WRONGCREDS':
        return '比特浏览器账号或密码不对，请在设置里重新填写'
    if code == 'VERIFY':
        return (
            '需要在比特浏览器里完成登录验证（短信/邮箱/二次验证），完成后重试'
        )
    return (
        '自动登录没有成功，请手动打开比特浏览器登录一次，然后重试任务'
        f'（{code}）'
    )


# The cloud-served "unknown id" message (observed live on /browser/open
# and /browser/detail with an unknown id, HTTP 200). The English
# wording is cloud-side and was not obtainable — unmatched open errors
# fall through to STORE_UNAVAILABLE carrying the vendor's message.
_NOT_FOUND_MARK = '没有找到相应数据'


class BitBrowserClient(LocalApiClient):
    """The BitBrowser local API (POST + JSON, 0-based pages, 10 req/s)."""

    vendor_key = 'bitbrowser'
    api_port = 54345
    list_method = 'POST'
    list_path = '/browser/list'
    open_method = 'POST'
    open_path = '/browser/open'
    close_method = 'POST'
    close_path = '/browser/close'
    # The documented limit is 10 req/s (HTTP 429 above it); 0.2 s keeps
    # us far below it while still spacing the pages of a big list.
    min_interval_s = 0.2
    # /browser/open can download a kernel on first use — give it room.
    request_timeout_s = 90.0
    page_size = 100
    # How long after_open waits for the kernel's initial tabs (the
    # workbench and platform tabs are created asynchronously, and the
    # workbench can appear a listing or two after the platform tab).
    after_open_deadline_s = 10.0
    after_open_delay_s = 1.0
    # Per-instance config cache. Class-level default; the first read
    # creates the instance attribute.
    _config_cache: dict | None = None
    # The platform host of the profile the current open_store flow is
    # driving (from /browser/detail), so after_open can prefer the
    # store's own tab over any other http(s) page; and whether the
    # client will open a workbench tab beside it (the profile's
    # workbench setting — a missing field means expected).
    _platform_host: str = ''
    _expects_workbench: bool = True
    # The most recent logged-out message (stashed by is_logged_out;
    # '' when never seen logged out).
    _last_logged_out_msg: str = ''

    # ── requests / pagination ───────────────────────────────────────

    def __init__(self, browser_config: dict | None = None):
        super().__init__(browser_config)
        self._read_login_credentials()
        # The persisted login policy (kick / ping-pong) under
        # VIBE_HOME; the seam tests swap.
        self._login_state = login.load_login_state(VIBE_SELLER_DIR)

    def _read_login_credentials(self) -> None:
        """The stored account + password (injected by the manager as
        bitbrowser_username / bitbrowser_password)."""
        self._login_username = str(self._cfg.get('bitbrowser_username') or '')
        self._login_password = str(self._cfg.get('bitbrowser_password') or '')

    # ── auto-login (the ONLY place it happens) ─────────────────────

    async def ensure_logged_in(self) -> None:
        """Auto-login an expired session through the client's own login
        page (option B). The ONE place auto-login runs:

        1. already signed in → return (and lift any kick refusal);
        2. classify the logged-out state — a DEVICE KICK (Login
           out！, one account per computer) or a recent auto-login
           (ping-pong) REFUSES with the kick hint until a sign-in we
           did not do;
        3. otherwise drive the login form over the shared inspector
           (generic selectors, native value setters), release it, then
           decide by the LOCAL API — poll /browser/list until
           success:true;
        4. failures (wrong credentials, a verification step, selectors
           missing, the inspector unavailable) → LOGIN_REQUIRED with a
           plain hint; the inspector lock is always released.
        """
        if await self.is_logged_in():
            self._login_state.record_signed_in()
            return
        reason = await self._logged_out_reason()
        refusal = self._login_state.refuse_auto_login(reason)
        if refusal is not None:  # kick or ping-pong — never loop
            self._login_state.record_kick()
            raise LoginRequired(_KICK_HINT)
        if not (self._login_username and self._login_password):
            raise LoginRequired(_NO_CREDENTIALS_HINT)
        result = await self._drive_login_form()
        if result != 'SUBMITTED':
            raise LoginRequired(_form_failure_hint(result))
        self._login_state.record_auto_login()
        if not await login.poll_until_signed_in(self.api_call):
            classification = await self._classify_login_failure()
            raise LoginRequired(_form_failure_hint(classification))
        self._login_state.record_signed_in()

    async def _logged_out_reason(self) -> str:
        """The logged-out message (for the kick classification).

        is_logged_in()'s probe already raised through OUR
        is_logged_out, which stashed the message; the extra call only
        runs when that stash is empty.
        """
        if self._last_logged_out_msg:
            return self._last_logged_out_msg
        try:
            status, payload = await self.api_call(
                'POST',
                '/browser/list',
                json_body={'page': 0, 'pageSize': 1},
            )
        except LoginRequired:
            return self._last_logged_out_msg
        except Exception:
            return ''
        if isinstance(payload, dict):
            return str(payload.get('msg') or '')
        return ''

    async def _drive_login_form(self) -> str:
        """Open the inspector, fill + submit the form, close it. The
        main pid is the API port's owner (Windows) or the bundle's
        main process (macOS) — never guessed."""
        session = login.make_session()
        pid = await self._client_main_pid()
        if not pid:
            return 'NOCLIENT'
        driver = login.LoginDriver(
            self._login_username, self._login_password, pid, session
        )
        return await driver.run()

    async def _client_main_pid(self) -> int | None:
        """The Electron main pid that owns the local API."""
        return await login.main_pid_for_platform(self._port or self.api_port)

    async def _classify_login_failure(self) -> str:
        session = login.make_session()
        pid = await self._client_main_pid()
        if not pid:
            return 'UNKNOWN'
        driver = login.LoginDriver(
            self._login_username, self._login_password, pid, session
        )
        return await driver.classify_failure()

    def list_request(self, page: int) -> tuple[str, dict | None]:
        return self.list_path, {'page': page, 'pageSize': self.page_size}

    def next_page(self, payload, page: int) -> int | None:
        data = payload.get('data') if isinstance(payload, dict) else None
        data = data or {}
        try:
            total = int(data.get('totalNum') or 0)
        except (TypeError, ValueError):
            return None
        if (page + 1) * self.page_size < total:
            return page + 1
        return None

    def open_request(self, ref: str) -> tuple[str, dict | None]:
        # queue=True serializes concurrent opens (the client's own
        # recommendation); NEVER ignoreDefaultUrls — it would skip the
        # very platform page the agent needs.
        return self.open_path, {'id': ref, 'queue': True}

    def close_request(self, ref: str) -> tuple[str, dict | None]:
        return self.close_path, {'id': ref}

    # ── discovery / auth (hooks the LocalApiClient base calls) ──────

    async def discover_port(self) -> int | None:
        """The port from the client's own config.json, else the default.

        The config is read ONCE per client instance and cached (a
        /mnt read per API call on WSL is not free); a token reset is
        handled by the 403 re-read in :meth:`auth_headers`.
        """
        cfg = await self._client_config()
        return port_from_address(cfg.get('localServerAddress'))

    async def auth_headers(self) -> dict[str, str]:
        """``x-api-key`` from the client's own config — only when the
        user turned 鉴权控制 on.

        The token is BitBrowser's, read from its settings file; it is
        never logged, stored or returned by us. The config is cached
        per client instance (each engine start builds a fresh client,
        so a reset token is picked up by the next task naturally).
        """
        cfg = await self._client_config()
        if cfg.get('enableAuth') and cfg.get('authToken'):
            return {'x-api-key': str(cfg['authToken'])}
        return {}

    async def _client_config(self) -> dict:
        """The cached config.json ({} when unavailable): first use
        reads the file, afterwards the cache serves every call."""
        if self._config_cache is None:
            self._config_cache = await read_client_config()
        return self._config_cache

    # ── responses ───────────────────────────────────────────────────

    def parse_stores(self, payload) -> list[StoreRef]:
        """List rows → bare StoreRefs (ALLOW-LIST; the rows carry the
        profiles' site credentials — userName/password/cookie/proxy
        passwords — none of which may cross this method).
        """
        if not isinstance(payload, dict):
            # A text/HTML body (a proxy in front of the port, a broken
            # client) must not read as "no stores" — the picker would
            # show an empty list for a broken client.
            raise StoreUnavailable(
                f'/browser/list answered a non-JSON body ({payload!r:.60})'
            )
        rows = (payload.get('data') or {}).get('list') or []
        out: list[StoreRef] = []
        for r in rows:
            if not isinstance(r, dict) or not r.get('id'):
                continue
            kernel = str(r.get('coreProduct') or '').lower()
            out.append(
                StoreRef(
                    ref=str(r['id']),
                    name=f'#{r.get("seq", "")} {r.get("name", "")}'.strip(),
                    platform=platform_hostname(r.get('platform')),
                    # Firefox-kernel profiles have no CDP endpoint and
                    # cannot be driven; anything not 'chrome' is too.
                    available=kernel == 'chrome',
                    unavailable_reason=(
                        ''
                        if kernel == 'chrome'
                        else UNAVAILABLE_UNSUPPORTED_KERNEL
                    ),
                    unavailable_detail=('' if kernel == 'chrome' else kernel),
                )
            )
        return out

    def parse_opened(self, payload) -> OpenedStore:
        data = payload.get('data') or {}
        ws = str(data.get('ws') or '')
        if ws:
            return opened_from_ws(ws)
        http_addr = str(data.get('http') or '')
        if http_addr:
            host, _, port = http_addr.rpartition(':')
            try:
                return OpenedStore(host or '127.0.0.1', int(port))
            except ValueError:
                pass
        raise CdpUnreachable('open returned no CDP endpoint')

    def is_logged_out(self, status: int, payload) -> bool:
        if not isinstance(payload, dict):
            return False
        msg = str(payload.get('msg') or '')
        matched = any(mark in msg for mark in _TOKEN_EXPIRED_MARKS)
        if matched:
            # The shared response path raises a BARE LoginRequired, so
            # this is the one place the logged-out REASON (expiry vs
            # device kick) is visible — stash it for ensure_logged_in.
            self._last_logged_out_msg = msg
        return matched

    def map_error(self, status: int, payload) -> InjectError | None:
        if not isinstance(payload, dict):
            return None
        if status == 403 and 'x-api-key' in str(payload.get('msg')):
            # The recorded 鉴权控制 body. The vendor's own recovery:
            # check the toggle in the client's settings, then retry.
            return ApiNotEnabled(
                '请在比特浏览器 → 设置 → Local API 里检查「鉴权控制」，'
                '再重试任务。'
            )
        msg = str(payload.get('msg') or '')
        if _NOT_FOUND_MARK in msg:
            return StoreNotFound(msg)
        if payload.get('success') is False and msg:
            # Carries the vendor's own message (e.g. 内核更新失败…, a
            # kernel download failure that is NOT an unsupported
            # kernel) — never swallow it into a generic failure.
            return StoreUnavailable(msg)
        return None

    # ── open: the kernel check runs before the open call ────────────

    async def open_store(self, ref: str) -> OpenedStore:
        """Refuse non-Chromium kernels BEFORE opening (the Firefox open
        shape is unverified — client 7.2.0 cannot even download that
        kernel — so it must never be relied on).
        """
        status, payload = await self.api_call(
            'POST', '/browser/detail', json_body={'id': ref}
        )
        if self.is_logged_out(status, payload):
            raise LoginRequired()
        err = self.map_error(status, payload)
        if err is not None:
            raise err
        if not isinstance(payload, dict):
            # A text/HTML body (a proxy in front of the port, a broken
            # client) must not raise AttributeError on payload.get.
            raise StoreUnavailable(
                f'/browser/detail answered a non-JSON body ({status})'
            )
        data = payload.get('data') or {}
        core = str(
            data.get('coreProduct')
            or (data.get('browserFingerPrint') or {}).get('coreProduct')
            or ''
        ).lower()
        # Remember the profile's platform (after_open prefers its tab)
        # and whether the client will open a 工作台 workbench tab beside
        # it (the workbench profile setting: 'disable' never shows one;
        # a missing field means the client default — shown).
        self._platform_host = platform_hostname(data.get('platform'))
        self._expects_workbench = str(data.get('workbench') or '') != 'disable'
        if core != 'chrome':
            # The message's prefix is quoted verbatim in the docs error
            # tables (docs/bitbrowser.md / .en.md § 常见提示 / 报错 /
            # Common statuses / errors), which gloss it in English.
            raise UnsupportedKernel(
                '这个窗口用的不是 Chrome（Chromium）内核，任务无法打开'
                '它；在比特浏览器里把这个窗口的内核切换为 Chrome'
                '（指纹 → 内核）后重试'
            )
        return await super().open_store(ref)

    # ── the workbench tab: closed over CDP, never by profile edits ──

    async def after_open(self, opened: OpenedStore) -> None:
        """Close the 工作台 tab so the agent lands on the store page.

        BitBrowser opens a workbench page (console.bitbrowser.net)
        beside the profile's platform page when the profile's
        ``workbench`` setting is not ``disable`` (open_store records
        that as ``_expects_workbench`` from /browser/detail — the
        user's profile is never edited for this; a ``disable`` change
        would silently alter how their own client behaves). Whether a
        workbench is expected decides what to wait for:

        - expected: poll until BOTH a workbench tab and a store tab
          exist (the workbench is created around the same moment as
          the platform tab — a listing in that gap must not be read as
          "already clean"), then close the workbench tab(s) and
          activate the store tab. A workbench that never appears
          within the deadline (the user closed it in a hand-opened
          profile) logs one warning and just activates the store tab.
        - not expected (``disable``): activate the store tab as soon
          as it exists — no waiting for a tab that will never come.

        The store tab to activate is an http(s) PAGE — never an
        extension or chrome:// page — preferring the profile's own
        platform host. The last tab is NEVER closed: while no store
        tab exists, the workbench stays (closing it would close the
        browser). Idempotent: a second run finds no workbench tab and
        only re-activates the store tab.
        """
        deadline = time.monotonic() + self.after_open_deadline_s
        store_tabs: list[dict] = []
        while True:
            pages = await _cdp_page_targets(opened)
            if pages is not None:
                workbench = [
                    p for p in pages if _WORKBENCH_HOST in str(p.get('url', ''))
                ]
                store_tabs = [
                    p for p in pages if p not in workbench and _is_http_page(p)
                ]
                if store_tabs and (workbench or not self._expects_workbench):
                    # ONE close-and-activate path.
                    for tab in workbench:
                        await _close_target(opened, tab)
                    await _activate_target(
                        opened, _preferred(store_tabs, self._platform_host)
                    )
                    if workbench:
                        logger.info(
                            'bitbrowser env on %s:%s: closed %d workbench '
                            'tab(s)',
                            opened.cdp_host,
                            opened.cdp_port,
                            len(workbench),
                        )
                    return
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(self.after_open_delay_s)
        # Deadline passed: activate what there is (a store tab without
        # the expected workbench, or nothing but non-store tabs).
        if store_tabs:
            if self._expects_workbench:
                logger.warning(
                    'bitbrowser env on %s:%s: no workbench tab appeared '
                    'within %.0f s; activating the store tab',
                    opened.cdp_host,
                    opened.cdp_port,
                    self.after_open_deadline_s,
                )
            await _activate_target(
                opened, _preferred(store_tabs, self._platform_host)
            )
        else:
            logger.warning(
                'bitbrowser env on %s:%s: no store tab appeared within '
                '%.0f s; continuing (a lone workbench tab is never '
                'closed — that would close the browser)',
                opened.cdp_host,
                opened.cdp_port,
                self.after_open_deadline_s,
            )

    # ── launch the client when it is not running ────────────────────

    async def ensure_running(self) -> None:
        """Probe the API; launch the client when it does not answer.

        A client that ANSWERS with an error payload (logged out, auth
        refused) is running — those concerns surface later in the flow,
        so they are not retried here. On a transport failure
        :meth:`launch_client` locates and starts the exe (the test seam:
        patched so tests never launch the real client), then /health is
        polled for up to 30 s. Still nothing → CLIENT_NOT_RUNNING.
        """
        if self._port is None:
            self._port = await self.discover_port() or self.api_port
        if await self._probe_running():
            return
        await self.launch_client()
        for _ in range(_LAUNCH_POLL_ATTEMPTS):
            await asyncio.sleep(_LAUNCH_POLL_DELAY_S)
            if await self._probe_running():
                return
        raise ClientNotRunning(
            'the BitBrowser client did not come up within '
            f'{_LAUNCH_POLL_ATTEMPTS * _LAUNCH_POLL_DELAY_S:.0f} s of '
            'launching it'
        )

    async def launch_client(self) -> None:
        """Locate the exe/bundle and launch it detached.

        Raises CLIENT_NOT_RUNNING with the install hint when nothing is
        found on disk. A separate method (not inline in ensure_running)
        so tests patch THIS seam instead of reaching into the flow.
        Windows/WSL locate the exe (the Windows app descriptor, or the
        saved client path); mac locates the app bundle through the
        shared macOS locator.
        """
        if get_platform() == 'mac':
            await self._launch_client_mac()
            return
        exe = await asyncio.to_thread(
            locate_win_app,
            BITBROWSER_WIN_APP,
            configured_path=self._cfg.get('client_path'),
        )
        if exe is None:
            raise ClientNotRunning(
                'could not find the BitBrowser (比特浏览器) client — '
                'install it from bitbrowser.net, or save its install '
                'path in Settings.'
            )
        if not await asyncio.to_thread(_launch_exe, exe.exe_path):
            raise ClientNotRunning(
                f'could not launch the BitBrowser client at '
                f'{exe.exe_path} (located via {exe.hint})'
            )

    async def _launch_client_mac(self) -> None:
        """Locate + ``open`` the app bundle via the shared macOS locator
        (its fixed interface: locate / not_found_message / launch, all
        blocking). A saved client path pointing at a KERNEL bundle
        (``Chrome-bin``/``Firefox-bin`` hold an English-named
        ``BitBrowser.app``/``BitFox.app`` that is NOT the client) is
        refused instead of launched.
        """
        saved = (self._cfg.get('client_path') or '').strip()
        if saved and any(
            mark in saved for mark in ('Chrome-bin', 'Firefox-bin')
        ):
            raise ClientNotRunning(
                f'the saved client path {saved!r} is a BitBrowser '
                'kernel bundle, not the client app — save the path of '
                '比特浏览器.app (usually in /Applications).'
            )
        located = await asyncio.to_thread(
            _mac_locator.locate, mac_app_descriptor(), saved or None
        )
        if located is None:
            raise ClientNotRunning(
                _mac_locator.not_found_message(mac_app_descriptor())
            )
        await asyncio.to_thread(_mac_locator.launch, located)

    async def _probe_running(self) -> bool:
        """True when the local API answers (any payload, even errors)."""
        try:
            await self.api_call('POST', '/health', json_body={})
            return True
        except ClientNotRunning:
            return False
        except InjectError:
            return True  # answered — running


# ── the vendor declaration ───────────────────────────────────────────


def make_client(browser_config: dict) -> BitBrowserClient:
    """Build the real client (tests swap this seam for a fake)."""
    return BitBrowserClient(browser_config)


# One login per machine, but NOTHING is stored by us: the user signs
# in once in the client and the local API acts as that login. The
# profiles the picker lists follow that login (own + authorized-to-it).
#
# R8, known issue: if the user signs 比特浏览器 into ANOTHER account on
# this machine, our bound stores fail with STORE_NOT_FOUND (the
# profile ids no longer exist for that login). One account per
# machine; not detected, not auto-resolved.
BITBROWSER_VENDOR = BrowserVendorSpec(
    key='bitbrowser',
    display_name_en='BitBrowser',
    display_name_zh='比特浏览器',
    kind=KIND_ANTIDETECT,
    platforms=_SUPPORTED_PLATFORMS,
    # 'single' (the #327 spec split): one machine login, stored so
    # the client can AUTO-LOG-IN an expired session (option B, the
    # user's binding decision — the password is Fernet-encrypted at
    # rest through the generic credentials route).
    account_cardinality=CARDINALITY_SINGLE,
    credential_fields=(
        VendorCredentialField('username', 'settings.bitbrowserUsername'),
        VendorCredentialField(
            'password', 'settings.bitbrowserPassword', secret=True
        ),
    ),
    lists_profiles=True,
    profiles_scope=PROFILES_LOGIN,
    picker_kind=PICKER_GENERIC,
    client_kind=CLIENT_LOCAL_API,
    i18n_prefix='vendors.bitbrowser',
    docs={
        'errors': VendorDocsLink(
            topic='bitbrowserErrors',
            zh=('https://docs.vibe-sellers.com/bitbrowser/#常见提示--报错'),
            en=(
                'https://docs.vibe-sellers.com/en/bitbrowser/'
                '#common-statuses--errors'
            ),
        ),
    },
    client_factory=make_client,
    win_app=BITBROWSER_WIN_APP,
    client_path_setting='bitbrowser_client_path',
)


class BitBrowserBackend(InjectCdpBackend):
    """Route-B backend for the BitBrowser anti-detect browser."""

    def __init__(self, client_factory: ClientFactory | None = None):
        super().__init__(client_factory or (lambda cfg: make_client(cfg)))

    async def start(self, browser_config: dict) -> BrowserSessionInfo:
        # Derived from the spec (enabling 'mac' later is one edit in the
        # spec, not a second gate here); the message lists the supported
        # platforms instead of naming one.
        platform = get_platform()
        supported = BITBROWSER_VENDOR.platforms
        if platform not in supported:
            names = '/'.join(sorted(supported))
            raise UnsupportedPlatform(
                f'bitbrowser supports {names}; this machine is {platform}'
            )
        return await super().start(browser_config)
