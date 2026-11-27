"""Local-API browser clients: one base, ~30–80 lines per vendor.

The ~25 anti-detect vendors whose desktop client exposes a local HTTP
API (list / start(id) → CDP / stop on 127.0.0.1) differ only in shapes:
which port, how to authenticate, the paths, how to parse the store list
and the opened endpoint, how to tell "logged out" and "API not enabled"
apart, and how fast calls may come. :class:`LocalApiClient` keeps
everything uniform as CLASS ATTRIBUTES and exposes the differences as
small overridable methods, so a vendor module is a short subclass of
this class plus a :class:`~app.browser.vendors.BrowserVendorSpec` —
never a new client.

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
``list/open/close_method`` + ``_path`` — the calls. Paths are str.format
                   templates; ``{ref}`` substitutes the store ref.
``min_interval_s`` minimum seconds between API calls — the vendors'
                   rate limits range from half a second to several
                   calls per second (see the research catalogue).

Vendor parts (overridable methods)
----------------------------------
``parse_stores(payload) -> list[StoreRef]``
``parse_opened(payload) -> OpenedStore`` — fill ``cdp_host``/``cdp_port``
  (the engine verifies the endpoint itself); :func:`opened_from_ws`
  parses a ws URL, and a port-only answer is just
  ``OpenedStore('127.0.0.1', port)``.
``is_logged_out(status, payload) -> bool`` — ONE function per vendor.
``map_error(status, payload) -> InjectError | None`` — ONE function per
  vendor, mapping raw responses onto the shared taxonomy. The paid-gate
  codes most vendors share (API not enabled / plan required — the #1
  support case) map to :class:`ApiNotEnabled`, defined here until the
  shared error table (``inject/errors.py``) grows them.

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
            self._port = await self._discover_port()
        if self._port is None:
            raise ClientNotRunning(
                f'no local API port found for {type(self).__name__}'
            )
        try:
            await self._api_call(self.list_method, self.list_path)
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
        status, payload = await self._api_call(self.list_method, self.list_path)
        if self.is_logged_out(status, payload):
            raise LoginRequired()
        err = self.map_error(status, payload)
        if err is not None:
            raise err
        return self.parse_stores(payload)

    async def open_store(self, ref: str) -> OpenedStore:
        status, payload = await self._api_call(
            self.open_method, self.open_path.format(ref=ref)
        )
        err = self.map_error(status, payload)
        if err is not None:
            raise err
        return self.parse_opened(payload)

    async def close_store(self, ref: str) -> None:
        """Best-effort; never raises for cleanup."""
        try:
            await self._api_call(
                self.close_method, self.close_path.format(ref=ref)
            )
        except Exception as e:  # cleanup must never raise
            logger.info('close_store(%s) failed (ignored): %s', ref, e)

    async def aclose(self) -> None:
        """Release the HTTP session (tests; long-lived callers)."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ── vendor parts (a vendor overrides these) ─────────────────────

    def parse_stores(self, payload) -> list[StoreRef]:
        raise NotImplementedError

    def parse_opened(self, payload) -> OpenedStore:
        raise NotImplementedError

    def is_logged_out(self, status: int, payload) -> bool:
        return False

    def map_error(self, status: int, payload) -> InjectError | None:
        return None

    # ── shared plumbing ─────────────────────────────────────────────

    async def _discover_port(self) -> int | None:
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

    def _auth_headers(self) -> dict[str, str]:
        if self.auth == 'none' or not self._auth_value:
            return {}
        if self.auth == 'bearer':
            return {'Authorization': f'Bearer {self._auth_value}'}
        return {self.auth_header: self._auth_value}

    async def _api_call(self, method: str, path: str) -> tuple[int, object]:
        """One rate-limited API call → (HTTP status, parsed payload).

        Transport failures raise ClientNotRunning; an API that answers
        with an error payload returns it for the vendor's map_error.
        """
        if self._port is None:
            raise ClientNotRunning('no API port resolved')
        await self._rate_limit()
        url = f'{self._base}:{self._port}{path}'
        session = await self._ensure_session()
        try:
            async with session.request(
                method,
                url,
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(
                    total=self.request_timeout_s,
                    connect=self.connect_timeout_s,
                ),
            ) as resp:
                return resp.status, await _json_or_text(resp)
        except (aiohttp.ClientError, TimeoutError) as e:
            raise ClientNotRunning(f'{method} {url}: {e}') from e

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
