"""Local-API browser clients: one base, ~30–80 lines per vendor.

The ~25 anti-detect vendors whose desktop client exposes a local HTTP
API (list / start(id) → CDP / stop on 127.0.0.1) differ only in shapes:
which port, how to authenticate, the requests, how to parse the store
list and the opened endpoint, how to tell "logged out" and "API not
enabled" apart, and how fast calls may come. :class:`LocalApiClient`
keeps everything uniform as CLASS ATTRIBUTES and exposes the
differences as small overridable methods, so a vendor module is a short
subclass of this class plus a
:class:`~app.browser.vendors.BrowserVendorSpec` — never a new client.

Uniform parts (class attributes)
--------------------------------
``vendor_key``     the spec key (credential fields are injected into the
                   browser config as ``<vendor_key>_<field>``).
``api_port``       default local port (``0`` → discovery only).
``discovery``      ``'fixed'`` | ``'scan'`` | ``'file'`` — how to find
                   the port when the default is wrong.
``scan_ports``     fallback ports for ``'scan'``.
``port_file``      file the client writes its port to (``'file'``).
``auth``           ``'none'`` | ``'header'`` | ``'bearer'`` — how the
                   API authenticates, using the value of the credential
                   field named by ``auth_field``.
``auth_header``    header name for ``'header'`` auth.
``list/open/close_method`` — the HTTP verbs.
``min_interval_s`` minimum seconds between API calls — the vendors'
                   rate limits range from half a second to several
                   calls per second (see the research catalogue); the
                   limit also spaces the pages of a paginated list.
``max_list_pages`` the pagination guard (below).
``connect/request_timeout_s`` — a hung connect (an unbound loopback
                   port on WSL does NOT refuse, it hangs) must fail
                   bounded.

Vendor parts (overridable methods)
----------------------------------
Requests — each returns ``(path, json_body | None)``:
``list_request(page)``   the list call for a 0-based page counter (a
   1-based vendor translates inside the hook); a JSON body is sent as
   the request body. Query parameters stay in the PATH string — a path
   is the full request target, so ``'…?user_id={ref}''`` is fine.
``open_request(ref)`` / ``close_request(ref)`` — same shape.
   The defaults reproduce path-only requests (``{ref}`` substituted),
   which is what path/query-string vendors use.

Pagination — ``next_page(payload, page) -> int | None``: return the
next 0-based page counter while more pages exist, None to stop. The
default is a single page (None always). ``list_stores`` loops the hook,
honouring the rate limit between pages and de-duplicating rows by ref;
a vendor whose hook never says stop is cut off at ``max_list_pages``
with an error naming the limit — never a silent truncation.

Responses:
``parse_stores(payload) -> list[StoreRef]``
``parse_opened(payload) -> OpenedStore`` — fill ``cdp_host``/``cdp_port``
  (the engine verifies the endpoint itself); :func:`opened_from_ws`
  parses a ws URL, and a port-only answer is just
  ``OpenedStore('127.0.0.1', port)``.
``discover_port() -> int | None`` — overridable port discovery (a
  vendor reading the client's own config file MUST use
  ``asyncio.to_thread`` for the read).
``auth_headers() -> dict`` — async and overridable (a token may come
  from a client file); called before every request; never logged.
``is_rate_limited(status, payload) -> bool`` — default ``status == 429``;
  on a hit the base waits and retries ONCE.
``is_logged_out(status, payload) -> bool`` — ONE function per vendor,
  checked on EVERY call in the shared response path (before the retry
  decision and before ``map_error``).
``map_error(status, payload) -> InjectError | None`` — ONE function per
  vendor, mapping raw responses onto the shared taxonomy. The paid-gate
  codes most vendors share (API not enabled / plan required — the #1
  support case) map to :class:`ApiNotEnabled`, defined here until the
  shared error table (``inject/errors.py``) grows them.

Secrets: list rows routinely carry each profile's site password,
cookies and proxy credentials. The base NEVER logs a raw payload or
request body — log lines carry the method, URL and error text only —
and ``parse_stores`` must return bare :class:`StoreRef` rows so nothing
secret can flow into a response (pinned by the contract suite).

Subclasses needing EXTRA endpoints (a kernel check, a health probe)
call :meth:`api_call` — the same shared response path the built-ins
use, with the rate limit, auth headers, logged-out check and
no-payload-logging rules applied.

Optional hooks: ``ensure_running`` (default: discover the port and
probe; override to launch the client), ``after_open``,
``ensure_logged_in`` (default: raise LoginRequired when logged out — a
local-API client cannot drive the vendor's login form).
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlsplit

import aiohttp

from app.browser.inject.client import InjectClient, OpenedStore, StoreRef
from app.browser.inject.errors import (
    ClientNotRunning,
    InjectError,
    LoginRequired,
)

logger = logging.getLogger(__name__)

_LOCALHOST = '127.0.0.1'


class ApiNotEnabled(InjectError):
    """The vendor gates its local API behind a paid plan / a switch.

    The most common support case for local-API vendors. Lives here (not
    in ``inject/errors.py``) only while that shared table is owned by a
    different in-flight PR; the code is stable either way.
    """

    code = 'API_NOT_ENABLED'
    hint = (
        'Enable the local API in the client (some vendors need a paid '
        'plan), then retry.'
    )


class PaginationOverflow(InjectError):
    """A vendor's pagination never said stop.

    Raised (never silently truncated) once ``max_list_pages`` pages have
    been collected, so a mis-parsed ``next_page`` cannot loop forever or
    quietly return a partial list.
    """

    code = 'PAGINATION_OVERFLOW'
    hint = 'The store list pagination did not terminate; report this.'


def opened_from_ws(ws_url: str, user_data_dir: str = '') -> OpenedStore:
    """An OpenedStore parsed from a ``ws://host:port/...`` URL."""
    parts = urlsplit(ws_url)
    return OpenedStore(
        cdp_host=parts.hostname or _LOCALHOST,
        cdp_port=parts.port or 0,
        user_data_dir=user_data_dir,
    )


class LocalApiClient(InjectClient):
    """A vendor client driven through the vendor's local HTTP API."""

    # ── uniform parts (a vendor sets these) ─────────────────────────
    vendor_key: str = ''
    api_port: int = 0
    discovery: str = 'fixed'
    scan_ports: tuple[int, ...] = ()
    port_file: str = ''
    auth: str = 'none'
    auth_field: str = ''
    auth_header: str = ''
    list_method: str = 'GET'
    list_path: str = ''
    open_method: str = 'POST'
    open_path: str = ''
    close_method: str = 'POST'
    close_path: str = ''
    min_interval_s: float = 0.0
    # A hung connect (an unbound loopback port on WSL does NOT refuse —
    # it hangs until the OS gives up) must fail bounded, in seconds.
    connect_timeout_s: float = 5.0
    request_timeout_s: float = 30.0
    # The pagination guard: a list that claims more pages than this is
    # cut off with PaginationOverflow, never a silent truncation.
    max_list_pages: int = 200

    def __init__(self, browser_config: dict | None = None):
        cfg = browser_config or {}
        self._cfg = cfg
        self._port: int | None = cfg.get('api_port') or None
        self._base: str = cfg.get('api_base_url') or f'http://{_LOCALHOST}'
        # The auth field's value, spec-injected as <vendor>_<field>.
        self._auth_value: str = (
            cfg.get(f'{self.vendor_key}_{self.auth_field}')
            if self.auth_field
            else ''
        )
        self._session: aiohttp.ClientSession | None = None
        self._last_call = 0.0
        self._call_lock = asyncio.Lock()

    # ── the InjectClient contract ───────────────────────────────────

    async def ensure_running(self) -> None:
        """Find the port and probe the API; raise ClientNotRunning.

        The default never launches anything (most clients are launched
        by hand or by the vendor's own machinery); a vendor that CAN
        launch its client overrides this. An API that ANSWERS with an
        error payload (logged out, paid gate) is running — those
        concerns surface later in the flow.
        """
        if self._port is None:
            self._port = await self.discover_port()
        if self._port is None:
            raise ClientNotRunning(
                f'no local API port found for {type(self).__name__}'
            )
        path, body = self.list_request(0)
        try:
            await self.api_call(self.list_method, path, json_body=body)
        except ClientNotRunning:
            raise
        except InjectError:
            return  # the API answered — it is running

    async def is_logged_in(self) -> bool:
        try:
            await self.list_stores()
            return True
        except LoginRequired:
            return False

    async def ensure_logged_in(self) -> None:
        # A local-API client cannot drive the vendor's login form; the
        # machine login (when the API needs one) is typed in the client.
        if not await self.is_logged_in():
            raise LoginRequired()

    async def list_stores(self) -> list[StoreRef]:
        """Every store, following the vendor's pagination to the end.

        Pages are spaced by the rate limit; rows are de-duplicated by
        ref (a vendor that repeats a row across pages yields it once).
        A pagination that never terminates fails with
        :class:`PaginationOverflow` instead of looping or truncating.
        """
        page = 0
        out: list[StoreRef] = []
        seen: set[str] = set()
        while True:
            if page >= self.max_list_pages:
                raise PaginationOverflow(
                    f'{type(self).__name__}: the store list still claimed '
                    f'a next page after {self.max_list_pages} pages'
                )
            path, body = self.list_request(page)
            status, payload = await self.api_call(
                self.list_method, path, json_body=body
            )
            err = self.map_error(status, payload)
            if err is not None:
                raise err
            for ref in self.parse_stores(payload):
                if ref.name not in seen:
                    seen.add(ref.name)
                    out.append(ref)
            nxt = self.next_page(payload, page)
            if nxt is None:
                return out
            page = nxt

    async def open_store(self, ref: str) -> OpenedStore:
        path, body = self.open_request(ref)
        status, payload = await self.api_call(
            self.open_method, path, json_body=body
        )
        err = self.map_error(status, payload)
        if err is not None:
            raise err
        return self.parse_opened(payload)

    async def close_store(self, ref: str) -> None:
        """Best-effort; never raises for cleanup."""
        try:
            path, body = self.close_request(ref)
            await self.api_call(self.close_method, path, json_body=body)
        except Exception as e:  # cleanup must never raise
            logger.info('close_store(%s) failed (ignored): %s', ref, e)

    async def aclose(self) -> None:
        """Release the HTTP session (tests; long-lived callers)."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ── vendor parts (a vendor overrides these) ─────────────────────

    def list_request(self, page: int) -> tuple[str, dict | None]:
        """The list call for a 0-based page counter.

        The default is a path-only request (single page unless
        :meth:`next_page` says otherwise); a POST-JSON vendor returns
        its body, a 1-based vendor translates the counter here.
        """
        return self.list_path, None

    def open_request(self, ref: str) -> tuple[str, dict | None]:
        """The open call; default is the path with ``{ref}`` substituted.

        Query parameters stay in the path string — a path is the full
        request target (``'…?user_id={ref}'``).
        """
        return self.open_path.format(ref=ref), None

    def close_request(self, ref: str) -> tuple[str, dict | None]:
        """The close call; default is the path with ``{ref}``."""
        return self.close_path.format(ref=ref), None

    def next_page(self, payload, page: int) -> int | None:
        """The next 0-based page counter, or None when the list ended."""
        return None

    def parse_stores(self, payload) -> list[StoreRef]:
        raise NotImplementedError

    def parse_opened(self, payload) -> OpenedStore:
        raise NotImplementedError

    def is_logged_out(self, status: int, payload) -> bool:
        return False

    def is_rate_limited(self, status: int, payload) -> bool:
        """Whether a response says 'slow down'.

        The default is an HTTP 429. A vendor that answers a 200 with a
        rate-limit body (e.g. 'Too many request per second') overrides
        this to recognise it. On a hit the base waits
        ``max(1.0, min_interval_s)`` and retries ONCE; the retried
        response goes to ``map_error`` as-is.
        """
        return status == 429

    def map_error(self, status: int, payload) -> InjectError | None:
        return None

    # ── shared plumbing ─────────────────────────────────────────────

    async def discover_port(self) -> int | None:
        """Find the local API port; None when nothing answers.

        Overridable — a vendor may read the port from the client's own
        config file (e.g. a ``config.json`` under ``%APPDATA%``). Any
        file read inside an override MUST go through
        ``asyncio.to_thread`` (no blocking I/O on the event loop). The
        default honours ``discovery``: 'fixed' (``api_port``), 'scan'
        (``api_port`` then ``scan_ports``), or 'file' (``port_file``).
        """
        if self.discovery == 'file' and self.port_file:
            try:
                text = await asyncio.to_thread(
                    _read_text_best_effort, self.port_file
                )
                port = int(text.strip())
                if 0 < port < 65536:
                    return port
            except (OSError, ValueError):
                logger.info(
                    '%s: port file %r unreadable',
                    type(self).__name__,
                    self.port_file,
                )
        if self.discovery == 'scan':
            for port in (self.api_port, *self.scan_ports):
                if port and await _port_answers(self._base, port):
                    return port
            return None
        return self.api_port or None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def auth_headers(self) -> dict[str, str]:
        """The auth headers for one request.

        Overridable and async because a vendor may read a token from a
        client file (read it with ``asyncio.to_thread``; a vendor may
        cache). The default builds them from the credential config:
        none, a named header, or a bearer token. Its result is never
        logged.
        """
        if self.auth == 'none' or not self._auth_value:
            return {}
        if self.auth == 'bearer':
            return {'Authorization': f'Bearer {self._auth_value}'}
        return {self.auth_header: self._auth_value}

    async def api_call(
        self, method: str, path: str, json_body: dict | None = None
    ) -> tuple[int, object]:
        """One API call → (HTTP status, parsed payload).

        THE shared response path — every call (the built-ins and a
        vendor's own extra endpoints, e.g. a kernel check or a health
        probe) goes through it, so all of them get the same rules:

        - rate-limited (``min_interval_s``), with the auth headers
          (:meth:`auth_headers`) attached;
        - a logged-out response raises :class:`LoginRequired` — checked
          BEFORE the rate-limit retry decision and before the caller's
          ``map_error``, so a vendor that answers a logged-out open
          with an HTTP-200 error body surfaces LOGIN_REQUIRED, not a
          mapped store error;
        - a rate-limited response (``is_rate_limited``) is retried
          ONCE after ``max(1.0, min_interval_s)``; the retried response
          goes to the caller's ``map_error`` as-is;
        - transport failures raise :class:`ClientNotRunning`;
        - payloads and bodies are never logged — list rows carry site
          passwords and cookies.
        """
        if self._port is None:
            raise ClientNotRunning('no API port resolved')
        url = f'{self._base}:{self._port}{path}'
        session = await self._ensure_session()
        for attempt in (0, 1):
            await self._rate_limit()
            headers = await self.auth_headers()
            try:
                async with session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    timeout=aiohttp.ClientTimeout(
                        total=self.request_timeout_s,
                        connect=self.connect_timeout_s,
                    ),
                ) as resp:
                    status = resp.status
                    payload = await _json_or_text(resp)
            except (aiohttp.ClientError, TimeoutError) as e:
                raise ClientNotRunning(f'{method} {url}: {e}') from e
            if self.is_logged_out(status, payload):
                raise LoginRequired()
            if attempt == 0 and self.is_rate_limited(status, payload):
                await asyncio.sleep(max(1.0, self.min_interval_s))
                continue
            return status, payload
        raise AssertionError('unreachable')  # pragma: no cover

    async def _rate_limit(self) -> None:
        """Space calls at least ``min_interval_s`` apart (vendor limits)."""
        if self.min_interval_s <= 0:
            return
        async with self._call_lock:
            wait = self._last_call + self.min_interval_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


def _read_text_best_effort(path: str) -> str:
    with open(path, encoding='utf-8', errors='replace') as fh:
        return fh.read()


async def _json_or_text(resp) -> object:
    try:
        return await resp.json()
    except (aiohttp.ContentTypeError, ValueError):
        return await resp.text()


async def _port_answers(base: str, port: int) -> bool:
    session = aiohttp.ClientSession()
    try:
        async with session.get(
            f'{base}:{port}/', timeout=aiohttp.ClientTimeout(total=1.0)
        ):
            return True
    except aiohttp.ClientError:
        return False
    finally:
        await session.close()
