"""
Chrome browser backend.

Launches a Chromium instance via Playwright and starts a CDPMuxProxy
so multiple browser-use daemon processes can share the same browser.
Same architecture as ZiniaoBackend — one browser per store, isolated
per-task tabs via the proxy.

Cookie/localStorage persists in ``~/.vibe-seller/browser_profiles/{slug}/``
across browser restarts — including SESSION cookies: see
``_enable_session_restore`` (without it, Chromium wipes session
cookies on every launch and Taobao-style logins die with the browser
process even though the profile dir persists).

What a page can see of the store browser is decided here too, see
``_launch_options``: it must look like a person's browser window, not
an automation run (platform risk control reads exactly that).
"""

import asyncio
import json
import logging
import pathlib
import socket
import urllib.parse

import aiohttp
from playwright.async_api import async_playwright

from app.browser import chromium_engine
from app.browser.base import BrowserBackend, BrowserSessionInfo
from app.browser.cdp_mux_proxy import CDPMuxProxy
from app.browser.downloads import prepare_download_dir
from app.config import LOCALHOST
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

PROFILES_DIR = VIBE_SELLER_DIR / 'browser_profiles'


def _clear_singleton_locks(user_data_dir: pathlib.Path) -> None:
    """Remove stale Chrome ``Singleton{Lock,Socket,Cookie}`` files.

    A crash/SIGKILL (or a killed prior launch on a reused profile)
    leaves these behind and the next ``launch_persistent_context``
    fails with "Failed to launch the browser process". Best-effort;
    only call when no Chrome is bound to the dir. Mirrors
    ``ziniao._clear_singleton_locks`` but kept local to avoid a
    backend→backend import.
    """
    for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
        try:
            (user_data_dir / name).unlink(missing_ok=True)
        except OSError as e:
            logger.debug('Singleton-lock cleanup skipped (%s)', e)


# Chromium can fail to launch on a transient dbus / shared-memory /
# port race, especially under CI container load. A single flake
# shouldn't kill a real user task — retry a couple of times before
# giving up. Empirically the second attempt almost always succeeds.
_LAUNCH_ATTEMPTS = 3
_LAUNCH_RETRY_BACKOFF = 0.5  # seconds; doubles per attempt

# The window a store browser opens with. The page fills the window
# (``no_viewport``), so this is also the page size before anyone
# resizes the window.
_WINDOW_SIZE = (1280, 800)


def store_proxy_option(browser_config: dict) -> dict | None:
    """Playwright ``proxy`` for the store's own proxy setting, or None.

    The store form saves ``proxy_server`` (``http://host:port``,
    ``socks5://host:port``, optionally ``http://user:pass@host:port``)
    and ``proxy_bypass`` (comma-separated hosts) into the store's
    browser_config. Chromium will not take credentials inside the server
    URL, so they move to Playwright's username/password, which answer
    the proxy's auth challenge.
    """
    server = str(browser_config.get('proxy_server') or '').strip()
    if not server:
        return None
    proxy: dict[str, str] = {'server': server}
    parts = urllib.parse.urlsplit(server if '://' in server else f'//{server}')
    if parts.username is not None:
        host = parts.netloc.rsplit('@', 1)[1]
        scheme = f'{parts.scheme}://' if parts.scheme else ''
        proxy['server'] = f'{scheme}{host}'
        proxy['username'] = urllib.parse.unquote(parts.username)
        proxy['password'] = urllib.parse.unquote(parts.password or '')
    bypass = str(browser_config.get('proxy_bypass') or '').strip()
    if bypass:
        proxy['bypass'] = bypass
    return proxy


def _launch_options(
    debug_port: int, headless: bool, proxy: dict | None = None
) -> dict:
    """Playwright launch options for a store browser.

    A store browser must look to a page like a person's browser window,
    because platform risk control (Taobao's 生意参谋 "abnormal access"
    prompt, Amazon's re-sign-in) reads what a page can see:

    * ``--disable-blink-features=AutomationControlled``: Playwright
      launches with ``--enable-automation`` over a debugging pipe, and
      either one makes ``navigator.webdriver`` true — the plainest "this
      is a bot" signal there is. The switch only changes what pages
      read; CDP control (input, screenshots, downloads, tabs) is
      unaffected.
    * ``no_viewport`` + ``--window-size``: Playwright's default pins
      every page to an emulated 1280x720 viewport that ignores the
      window around it. The page now fills the real window, like the
      Ziniao and winchrome backends.
    * ``channel='chromium'``: the full Chromium build in both modes.
      Headless otherwise runs ``chromium-headless-shell``, which drops
      every SESSION cookie at startup (it ignores the restore-on-startup
      pref ``_enable_session_restore`` sets) — switching to headless
      logged every Taobao-style store out.

    ``proxy`` (from ``store_proxy_option``) routes the browser's traffic
    through the store's own proxy; loopback always goes direct.
    """
    width, height = _WINDOW_SIZE
    args = [
        f'--remote-debugging-port={debug_port}',
        '--no-first-run',
        '--disable-default-apps',
        '--disable-popup-blocking',
        '--disable-blink-features=AutomationControlled',
        f'--window-size={width},{height}',
    ]
    if headless:
        args.append('--disable-gpu')
    options = {
        'channel': 'chromium',
        'headless': headless,
        'no_viewport': True,
        'args': args,
    }
    if proxy:
        options['proxy'] = proxy
    return options


def _free_port() -> int:
    """Allocate a free TCP port (kernel picks one)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


def _enable_session_restore(user_data_dir: pathlib.Path) -> None:
    """Make SESSION cookies survive browser restarts for this profile.

    Store logins like Taobao/Tmall are carried by session cookies
    (``cookie2`` / ``_tb_token_`` / ``unb`` … all ``is_persistent=0``,
    verified live 2026-06-06). Chromium deletes session cookies at
    profile startup UNLESS the profile is in "continue where you left
    off" mode (``session.restore_on_startup == 1``). Without this
    pref, every browser restart (server restart, stop_session, crash)
    silently logged the store out even though the profile directory
    persisted — the login only lived as long as the chrome process.

    Called before every launch: Chrome rewrites Preferences from its
    in-memory copy on exit, so a one-time write would be clobbered by
    the first clean shutdown after it.
    """
    prefs_path = user_data_dir / 'Default' / 'Preferences'
    prefs: dict = {}
    if prefs_path.is_file():
        try:
            prefs = json.loads(prefs_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            logger.warning(
                'Unreadable Preferences at %s — rewriting minimal file',
                prefs_path,
            )
            prefs = {}
    session = prefs.setdefault('session', {})
    if session.get('restore_on_startup') == 1:
        return
    session['restore_on_startup'] = 1
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    prefs_path.write_text(
        json.dumps(prefs, ensure_ascii=False), encoding='utf-8'
    )
    logger.info(
        'Enabled session-cookie persistence (restore_on_startup=1) '
        'for profile %s',
        user_data_dir.name,
    )


class ChromeBackend(BrowserBackend):
    """Launch Chromium + CDPMuxProxy for a store."""

    def __init__(self):
        self._proxy: CDPMuxProxy | None = None
        self._pw = None  # Playwright instance
        self._context = None  # Playwright BrowserContext (persistent)
        self._browser = None  # Playwright Browser object

    async def start(self, browser_config: dict) -> BrowserSessionInfo:
        proxy_port = int(browser_config.get('proxy_port', 9222))
        # `headless` is injected by BrowserManager from the
        # `browser_headless` app_setting before reaching here. The
        # False fallback only fires for direct callers (tests, demo
        # runtime) — production always passes an explicit value.
        headless = bool(browser_config.get('headless', False))
        store_slug = browser_config.get('store_slug', 'default')
        proxy = store_proxy_option(browser_config)

        # Persistent profile directory — survives browser restarts.
        # Managed by stores.py (rename/delete on store CRUD).
        user_data_dir = PROFILES_DIR / store_slug
        user_data_dir.mkdir(parents=True, exist_ok=True)
        # A crashed/killed prior launch (or a lazily-restarted aux
        # browser reusing this profile) leaves Singleton{Lock,Socket,
        # Cookie} behind; the next launch_persistent_context then dies
        # with "Failed to launch the browser process" (observed live —
        # the aux browser failed to start on every retry until these
        # were cleared). Safe here: start() only runs when no Chrome is
        # bound to this dir.
        _clear_singleton_locks(user_data_dir)
        # Must run before EVERY launch (Chrome clobbers Preferences on
        # exit) so Taobao-style session-cookie logins survive restarts.
        await asyncio.to_thread(_enable_session_restore, user_data_dir)

        # Allocate a free port for Chrome's CDP debugging endpoint.
        debug_port = _free_port()

        # The Chromium this Playwright pins may still be downloading (the
        # first boot after an upgrade that bumped Playwright): fail fast
        # with that, not Playwright's "Executable doesn't exist".
        await chromium_engine.ensure_ready_for_launch()

        # Launch Chromium via Playwright — handles binary discovery,
        # platform-specific sandbox flags, and graceful shutdown.
        # Must use launch_persistent_context() for user_data_dir
        # (Playwright rejects --user-data-dir as a launch arg).
        self._pw = await async_playwright().start()

        logger.info(
            'Launching Chrome for store %s (debug_port=%d, headless=%s, '
            'proxy=%s)',
            store_slug,
            debug_port,
            headless,
            proxy['server'] if proxy else 'none',
        )
        last_exc: Exception | None = None
        for attempt in range(1, _LAUNCH_ATTEMPTS + 1):
            try:
                self._context = (
                    await self._pw.chromium.launch_persistent_context(
                        user_data_dir=str(user_data_dir),
                        **_launch_options(debug_port, headless, proxy),
                    )
                )
                self._browser = self._context.browser
                # Wait for CDP endpoint to be ready.
                await self._wait_for_cdp(debug_port)
                break
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    'Chrome launch attempt %d/%d failed for store %s: %s',
                    attempt,
                    _LAUNCH_ATTEMPTS,
                    store_slug,
                    exc,
                )
                # Tear down anything that did come up so the next
                # attempt starts from a clean slate.
                if self._context is not None:
                    try:
                        await self._context.close()
                    except Exception:
                        logger.debug(
                            'Cleanup of partial Chrome context failed',
                            exc_info=True,
                        )
                    self._context = None
                self._browser = None
                if attempt == _LAUNCH_ATTEMPTS:
                    # Give up — propagate the original error
                    await self._pw.stop()
                    self._pw = None
                    raise
                # Try a fresh debug port on each retry to avoid a
                # collision in case the previous Chrome already
                # bound the port before crashing.
                debug_port = _free_port()
                await asyncio.sleep(
                    _LAUNCH_RETRY_BACKOFF * (2 ** (attempt - 1))
                )
        else:  # pragma: no cover  - loop always returns/raises above
            raise last_exc  # type: ignore[misc]

        # Stable per-store download directory, emptied of the previous
        # session's files so "the newest match" and "the only match" are
        # the same file. See app/browser/downloads.py.
        dl_dir = await asyncio.to_thread(prepare_download_dir, store_slug)

        # Start CDPMuxProxy: listens on proxy_port, connects
        # upstream to Chrome's debug_port.  Same as Ziniao flow.
        self._proxy = CDPMuxProxy(
            listen_port=proxy_port,
            target_port=debug_port,
            target_host=LOCALHOST,
            download_dir=str(dl_dir),
            instance_id=browser_config.get('vibe_instance_id'),
            listen_sock=browser_config.get('proxy_socket'),
        )
        await self._proxy.start()

        logger.info(
            'CDPMuxProxy ready for Chrome store %s: %s:%d -> %s:%d '
            '(instance %s)',
            store_slug,
            LOCALHOST,
            proxy_port,
            LOCALHOST,
            debug_port,
            self._proxy.instance_id or '<none>',
        )

        # Verify proxy connectivity.
        test_url = f'http://{LOCALHOST}:{proxy_port}/json/version'
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    test_url,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    data = await resp.json()
                    if data.get('webSocketDebuggerUrl'):
                        logger.debug('Chrome CDP proxy connectivity OK')
                    else:
                        logger.warning(
                            'Chrome CDP proxy: unexpected response: %s',
                            data,
                        )
        except Exception as e:
            logger.warning('Chrome CDP proxy connectivity test failed: %s', e)

        # Extract PID from the browser (accessible via context.browser
        # for persistent contexts, or directly if available).
        pid = None
        browser = self._context.browser if self._context else self._browser
        if browser and hasattr(browser, 'process') and browser.process:
            pid = browser.process.pid

        return BrowserSessionInfo(
            cdp_port=debug_port,
            pid=pid,
        )

    async def stop(self, info: BrowserSessionInfo | None = None) -> None:
        # ``info`` is unused (teardown works off the instance's own
        # handles) but kept for the BrowserBackend interface. It defaults
        # to None so lifecycle owners that don't track a SessionInfo —
        # e.g. the aux browser — can call ``stop()`` to release the proxy,
        # context and Playwright driver instead of raising TypeError and
        # leaking a live Chromium.
        try:
            if self._proxy:
                if self._proxy.has_active_clients():
                    logger.warning(
                        'CDPMuxProxy still has active clients — stopping anyway'
                    )
                await self._proxy.stop()
                self._proxy = None
        except Exception as e:
            logger.warning('Error stopping Chrome proxy: %s', e)

        try:
            if self._context:
                await self._context.close()
                self._context = None
                self._browser = None
        except Exception as e:
            logger.warning('Error closing Chrome browser context: %s', e)

        try:
            if self._pw:
                await self._pw.stop()
                self._pw = None
        except Exception as e:
            logger.warning('Error stopping Playwright: %s', e)

    @staticmethod
    async def _wait_for_cdp(port: int, timeout: float = 30.0) -> None:
        """Poll CDP /json/version until ready."""
        url = f'http://{LOCALHOST}:{port}/json/version'
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as resp:
                        if resp.status == 200:
                            return
            except Exception:
                pass
            await asyncio.sleep(0.5)
        raise RuntimeError(
            f'Chrome CDP not ready at port {port} after {timeout}s'
        )
