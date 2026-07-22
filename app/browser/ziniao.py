"""
Ziniao anti-detect browser backend.

Communicates with the ziniao client via HTTP API to start/stop browser profiles,
then starts a CDP proxy so the agent's MCP Playwright can connect.

Credentials are loaded from ZiniaoAccount (via ziniao_account_id on the Store)
and passed in through the browser_config dict from BrowserManager.
"""

import asyncio
import json
import logging
from pathlib import Path
import uuid

import aiohttp

from app.browser.base import BrowserBackend, BrowserSessionInfo
from app.browser.cdp_mux_proxy import CDPMuxProxy
from app.browser.ziniao_utils import (
    ensure_ziniao_running,
    is_wsl,
    try_connect_ziniao,
    ziniao_host,
)
from app.config import DOWNLOADS_DIR, LOCALHOST

logger = logging.getLogger(__name__)

# updateCore downloads all browser kernels. Run it once per process,
# serialized so concurrent fan-out starts don't each loop the API, and
# best-effort/bounded so a slow or unreachable client can't block a start
# (kernels missing → startBrowser stales → per-store retry handles it).
_core_updated = False
_core_lock = asyncio.Lock()


async def update_ziniao_core(socket_port: int, user_info: dict) -> None:
    """Download browser kernels once before opening stores (best-effort).

    Mirrors the official demo's ``update_core``. Bounded and serialized so
    it can never multi-minute-block concurrent starts; see
    docs/ziniao-concurrency.md.
    """
    global _core_updated
    async with _core_lock:
        if _core_updated:
            return
        data = {
            'action': 'updateCore',
            'requestId': str(uuid.uuid4()),
            **user_info,
        }
        for _ in range(6):
            result, _h = await try_connect_ziniao(socket_port, data, timeout=15)
            if result is None:
                await asyncio.sleep(2)
                continue
            code = str(result.get('statusCode'))
            if code in ('0', '-10003'):
                _core_updated = True  # done, or client too old to support it
                return
            await asyncio.sleep(2)


def _clear_singleton_locks(userdata_dir: str | None) -> None:
    """Remove stale Chrome Singleton* files from a store's user-data dir.

    A crash or SIGKILL leaves ``SingletonLock``/``SingletonSocket``/
    ``SingletonCookie`` behind; the next Chrome launch then comes up without
    binding its remote-debugging port (the "stale launch"). Safe only when
    no Chrome is using the dir — callers invoke this after stopBrowser.
    Best-effort: never raises. See docs/ziniao-concurrency.md.
    """
    if not userdata_dir:
        return
    try:
        base = Path(userdata_dir)
        for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
            f = base / name
            if f.exists() or f.is_symlink():
                f.unlink(missing_ok=True)
                logger.info('Cleared stale Chrome %s in %s', name, base.name)
    except Exception as e:
        logger.debug('Singleton-lock cleanup skipped (%s)', e)


class ZiniaoBackend(BrowserBackend):
    """Start/stop ziniao browser profiles and connect via CDP."""

    def __init__(self):
        self._proxy: CDPMuxProxy | None = None
        # Per-store stopBrowser payload + control-app port, captured at
        # start() so stop() can actually terminate the Ziniao env (not
        # just the mux proxy). Per-store safe: stopBrowser closes THIS
        # env only, never the shared Ziniao client.
        self._stop_data: dict | None = None
        self._socket_port: int | None = None

    async def start(self, browser_config: dict) -> BrowserSessionInfo:
        # Credentials come from ZiniaoAccount (injected by BrowserManager)
        company = browser_config.get('company', '')
        username = browser_config.get('username', '')
        password = browser_config.get('password', '')
        socket_port = int(browser_config.get('socket_port', 16851))
        client_path = browser_config.get('client_path', 'ziniao')
        browser_oauth = browser_config.get('browser_oauth', '')
        proxy_port = int(browser_config.get('proxy_port', 9222))

        user_info = {
            'company': company,
            'username': username,
            'password': password,
        }

        # Ensure ziniao is running, launch if needed
        await ensure_ziniao_running(socket_port, client_path, user_info)

        # Download all browser kernels once (before opening any store) so a
        # per-store startBrowser never blocks on a kernel download. Mirrors
        # the official ziniao_webdriver demo. Best-effort.
        await update_ziniao_core(socket_port, user_info)

        # Start browser profile via ziniao API
        start_data = {
            'action': 'startBrowser',
            'isWaitPluginUpdate': 0,
            'isHeadless': 0,
            'requestId': str(uuid.uuid4()),
            'isWebDriverReadOnlyMode': 0,
            'cookieTypeLoad': 0,
            'cookieTypeSave': 0,
            'runMode': '1',
            'isLoadUserPlugin': False,
            'pluginIdType': 1,
            'privacyMode': 0,
            'notPromptForDownload': 1,
            **user_info,
        }
        if browser_oauth.isdigit():
            start_data['browserId'] = browser_oauth
        else:
            start_data['browserOauth'] = browser_oauth

        # Ziniao's startBrowser is flaky: it can return statusCode 0 + a
        # debuggingPort whose DevTools never initialises ("stale launch").
        # This is NONDETERMINISTIC and PER-STORE (verified — see
        # docs/ziniao-concurrency.md). Recover per store: stopBrowser this
        # env and retry startBrowser. NEVER restart the shared Ziniao client
        # here — a client kill destroys every OTHER store's live browser and
        # cascades into a machine-wide outage. We probe the raw port after
        # each startBrowser, bounded to MAX_ZINIAO_ATTEMPTS.
        stop_data: dict = {'action': 'stopBrowser', **user_info}
        if browser_oauth.isdigit():
            stop_data['browserId'] = browser_oauth
        else:
            stop_data['browserOauth'] = browser_oauth
        self._stop_data = stop_data
        self._socket_port = socket_port
        MAX_ZINIAO_ATTEMPTS = 4
        cdp_port = None
        target_host = None
        for attempt in range(1, MAX_ZINIAO_ATTEMPTS + 1):
            logger.info(
                'Starting ziniao browser (oauth=%s, port=%d, attempt %d/%d)',
                browser_oauth,
                socket_port,
                attempt,
                MAX_ZINIAO_ATTEMPTS,
            )
            # try_connect_ziniao handles mirrored (127.0.0.1) and NAT
            # (gateway IP) modes on WSL.
            result, host_used = await try_connect_ziniao(
                socket_port, start_data, timeout=60
            )
            if not result or str(result.get('statusCode')) != '0':
                # Chrome killed but control app alive (stale browser
                # state): stopBrowser to clean up, then retry once.
                logger.warning(
                    'startBrowser failed (%s), attempting stopBrowser + retry',
                    json.dumps(result, ensure_ascii=False)
                    if result
                    else 'no response',
                )
                await try_connect_ziniao(socket_port, stop_data, timeout=10)
                result, host_used = await try_connect_ziniao(
                    socket_port, start_data, timeout=60
                )
                if not result or str(result.get('statusCode')) != '0':
                    detail = (
                        json.dumps(result, ensure_ascii=False)
                        if result
                        else 'no response'
                    )
                    raise RuntimeError(
                        f'Ziniao startBrowser failed after '
                        f'stopBrowser retry: {detail}'
                    )
            logger.info('Connected to Ziniao via %s', host_used)

            cdp_port = result.get('debuggingPort')
            if not cdp_port:
                raise RuntimeError(
                    f'Ziniao response missing debuggingPort: {result}'
                )
            # Resolve the host where Ziniao's CDP is listening: 127.0.0.1
            # in mirrored mode, else the gateway IP (NAT mode).
            target_host = LOCALHOST if host_used == LOCALHOST else ziniao_host()
            logger.info(
                'Ziniao browser started, CDP port: %s (host %s)',
                cdp_port,
                target_host,
            )

            # Verify the browser ACTUALLY came up on that port before we
            # build the proxy against it.
            if await self._cdp_port_reachable(target_host, int(cdp_port)):
                break

            logger.warning(
                'Ziniao reported startBrowser success but CDP %s:%s is '
                'unreachable (stale launch) — attempt %d/%d.',
                target_host,
                cdp_port,
                attempt,
                MAX_ZINIAO_ATTEMPTS,
            )
            if attempt >= MAX_ZINIAO_ATTEMPTS:
                raise RuntimeError(
                    f'Ziniao reported a browser on {target_host}:{cdp_port} '
                    f'but nothing is reachable there after '
                    f'{MAX_ZINIAO_ATTEMPTS} startBrowser attempts. This '
                    f'store failed to launch; other stores are unaffected. '
                    f'Retry the task — if it persists, the Ziniao client may '
                    f'need a manual restart (Settings → Ziniao).'
                )
            # Per-store recovery: close THIS env and let the loop retry
            # startBrowser. Deliberately no shared-client restart — that
            # would tear down every other store's live browser.
            await try_connect_ziniao(socket_port, stop_data, timeout=10)
            # A common cause of stale launches: an earlier unclean shutdown
            # (crash / SIGKILL) left stale Chrome SingletonLock/Socket/Cookie
            # files in this store's user-data dir, so Chrome comes up without
            # binding its debug port. stopBrowser closed the env, so it's now
            # safe to clear them before retrying. See docs/ziniao-concurrency.md.
            _clear_singleton_locks(result.get('userData'))
        # cdp_port / target_host now point at a reachable browser.

        # Stable per-store download directory so every browser-use
        # CLI invocation saves files to the same place (instead of
        # each creating a random /tmp/browser-use-downloads-*/ dir).
        slug = browser_config.get('store_slug', 'default')
        dl_dir = DOWNLOADS_DIR / slug
        dl_dir.mkdir(parents=True, exist_ok=True)

        # Start multi-client CDP proxy: listens on 127.0.0.1:proxy_port,
        # connects upstream (WebSocket) to target_host:cdp_port.
        # Multiple browser-use CLI processes connect via
        # ws://127.0.0.1:{proxy_port}/client-{task_id}.
        # keep_last_page=True: Ziniao opens each env on a single
        # launcher/seller-central page that IS the environment's window.
        # The mux's startup orphan-tab cleanup must never close the last
        # page — closing it closes the browser and tears the whole
        # environment down on Ziniao 6.26.x (network SDK aborts with
        # 'ContainerId is missing', CDP dies ~15s later). Verified:
        # closing the env's last page kills it even 25s after it is fully
        # established; keeping any one page open keeps the env alive.
        async def _relaunch_upstream() -> tuple[int, str] | None:
            """Self-heal hook for the mux proxy — re-open THIS store only.

            Ziniao's ``debuggingPort`` rotates on every startBrowser and
            dies when the browser goes away (e.g. a server restart tore down
            the in-process proxy and the upstream port is now stale), so a
            proxy that only retries the original port serves 502 forever.
            This re-opens *this store's* env (stopBrowser + startBrowser) and
            returns the FRESH reachable ``(port, host)`` so the proxy can
            repoint. It deliberately does NOT restart the shared Ziniao
            client — that would tear down every OTHER store's live browser
            and cascade (see docs/ziniao-concurrency.md). Returns None when
            it cannot recover.
            """
            try:
                await try_connect_ziniao(socket_port, stop_data, timeout=10)
                result, host_used = await try_connect_ziniao(
                    socket_port, start_data, timeout=60
                )
                if not result or str(result.get('statusCode')) != '0':
                    return None
                new_port = result.get('debuggingPort')
                if not new_port:
                    return None
                new_host = (
                    LOCALHOST if host_used == LOCALHOST else ziniao_host()
                )
                if not await self._cdp_port_reachable(new_host, int(new_port)):
                    return None
                logger.info(
                    'Ziniao upstream self-healed (per-store): fresh CDP %s:%s',
                    new_host,
                    new_port,
                )
                return int(new_port), new_host
            except Exception as e:
                logger.warning('Ziniao upstream self-heal failed: %s', e)
                return None

        self._proxy = CDPMuxProxy(
            listen_port=proxy_port,
            target_port=int(cdp_port),
            target_host=target_host,
            download_dir=str(dl_dir),
            keep_last_page=True,
            relaunch_upstream=_relaunch_upstream,
        )
        await self._proxy.start()

        logger.info(
            'CDPMuxProxy ready: %s:%d -> %s:%s',
            LOCALHOST,
            proxy_port,
            target_host,
            cdp_port,
        )

        # Verify the proxy can connect to the browser via HTTP.
        test_timeout = 5
        test_url = f'http://{LOCALHOST}:{proxy_port}/json/version'
        logger.debug(
            'Testing CDP proxy connectivity (%s, timeout=%ds)...',
            test_url,
            test_timeout,
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    test_url,
                    timeout=aiohttp.ClientTimeout(total=test_timeout),
                ) as resp:
                    data = await resp.json()
                    if data.get('webSocketDebuggerUrl'):
                        logger.debug('CDP proxy connectivity test passed')
                    else:
                        logger.warning(
                            'CDP proxy test: unexpected response: %s',
                            data,
                        )
        except TimeoutError:
            if is_wsl() and target_host != LOCALHOST:
                raise RuntimeError(
                    f'Cannot connect to Ziniao browser debugging port '
                    f'({target_host}:{cdp_port}). '
                    f'\n\nIf you are using WSL2 NAT mode, the browser is '
                    f'listening on Windows localhost, which is not '
                    f'accessible from WSL via the gateway IP. '
                    f'\n\nTo fix this, either:'
                    f'\n  Option 1: Switch WSL to mirrored networking mode:'
                    f'\n    1. Edit C:\\Users\\%USERNAME%\\.wslconfig'
                    f'\n    2. Change networkingMode=nat to '
                    f'networkingMode=mirrored'
                    f'\n    3. Run: wsl --shutdown'
                    f'\n    4. Restart WSL and the server'
                    f'\n  Option 2: Restart Ziniao to get a fresh debugging port'
                ) from None
            raise RuntimeError(
                f'CDP proxy connection test timed out after {test_timeout}s. '
                f'The browser may not be accepting connections.'
            ) from None
        except Exception as e:
            logger.warning('CDP proxy connectivity test failed: %s', e)

        # Ziniao may destroy externally-created targets while
        # still initialising.  Verify a test target survives
        # before declaring the session ready.
        await self._wait_for_target_stability(proxy_port)

        return BrowserSessionInfo(
            cdp_port=int(cdp_port),
        )

    @staticmethod
    async def _cdp_port_reachable(
        host: str,
        port: int,
        *,
        attempts: int = 4,
        delay: float = 2.0,
    ) -> bool:
        """True if Chrome's CDP endpoint answers on host:port.

        Retries a few times over ~6s so a slow-but-healthy launch is not
        mistaken for the stale-launch state (which never binds the port
        at all). Probes the RAW Ziniao port directly, before the mux
        proxy is built against it.
        """
        url = f'http://{host}:{port}/json/version'
        for i in range(attempts):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        data = await resp.json()
                        if data.get('webSocketDebuggerUrl'):
                            return True
            except Exception:
                pass
            if i < attempts - 1:
                await asyncio.sleep(delay)
        return False

    @staticmethod
    async def _wait_for_target_stability(
        proxy_port: int,
        *,
        max_retries: int = 3,
        survival_secs: float = 5.0,
    ) -> None:
        """Create a test target and verify Ziniao doesn't kill it.

        Creates a target with a real HTTP URL (not about:blank)
        because Ziniao may only destroy targets that trigger
        navigation/redirects during its late-stage initialization.
        The blank page survives but navigated pages get killed.

        If the target is destroyed within ``survival_secs``,
        wait and retry.  This guards against the race where
        the CDP proxy responds to /json/version but Ziniao's
        browser hasn't finished initialising yet.
        """
        ws_url = (
            f'ws://{LOCALHOST}:{proxy_port}'
            f'/client-stability-{uuid.uuid4().hex[:8]}'
        )
        for attempt in range(1, max_retries + 1):
            destroyed = False
            target_id = None
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, timeout=10) as ws:
                        # Create test target with a real URL
                        # (about:blank doesn't trigger navigation
                        # so it gives false confidence)
                        await ws.send_json({
                            'id': 1,
                            'method': 'Target.createTarget',
                            'params': {
                                'url': 'https://example.com',
                            },
                        })
                        # Wait for createTarget response and
                        # watch for targetDestroyed events.
                        deadline = (
                            asyncio.get_event_loop().time() + survival_secs
                        )
                        while asyncio.get_event_loop().time() < deadline:
                            remaining = (
                                deadline - asyncio.get_event_loop().time()
                            )
                            if remaining <= 0:
                                break
                            try:
                                msg = await asyncio.wait_for(
                                    ws.receive_json(),
                                    timeout=remaining,
                                )
                            except TimeoutError:
                                break
                            # Capture target ID from response
                            if msg.get('id') == 1 and 'result' in msg:
                                target_id = msg['result'].get('targetId')
                            # Check for destruction
                            if msg.get('method') == 'Target.targetDestroyed':
                                tid = msg.get('params', {}).get('targetId', '')
                                if tid == target_id:
                                    destroyed = True
                                    break

                        # Clean up test target
                        if target_id and not destroyed:
                            await ws.send_json({
                                'id': 2,
                                'method': 'Target.closeTarget',
                                'params': {
                                    'targetId': target_id,
                                },
                            })
                            try:
                                await asyncio.wait_for(
                                    ws.receive_json(),
                                    timeout=2,
                                )
                            except TimeoutError:
                                pass

            except Exception as e:
                logger.warning(
                    'Target stability check attempt %d failed: %s',
                    attempt,
                    e,
                )
                destroyed = True

            if not destroyed:
                logger.info(
                    'Ziniao target stability confirmed (attempt %d, %.1fs)',
                    attempt,
                    survival_secs,
                )
                return

            logger.warning(
                'Ziniao target destroyed within %.1fs '
                '(attempt %d/%d), waiting for init...',
                survival_secs,
                attempt,
                max_retries,
            )
            if attempt < max_retries:
                await asyncio.sleep(2 * attempt)  # backoff

        logger.warning(
            'Ziniao target stability not confirmed after %d '
            'retries — proceeding anyway',
            max_retries,
        )

    async def stop(self, info: BrowserSessionInfo) -> None:
        try:
            if self._proxy:
                if self._proxy.has_active_clients():
                    logger.warning(
                        'CDPMuxProxy still has active clients — stopping anyway'
                    )
                await self._proxy.stop()
                self._proxy = None
        except Exception as e:
            logger.warning(f'Error stopping ziniao backend: {e}')
        # Terminate the Ziniao browser ENV itself (per-store stopBrowser
        # — never the shared client). Without this, stop() only tore
        # down the proxy and the Chromium env lived forever, piling up
        # tabs until store deletion or a machine reboot.
        if self._stop_data and self._socket_port:
            try:
                await try_connect_ziniao(
                    self._socket_port, self._stop_data, timeout=10
                )
                logger.info('Ziniao env stopped (per-store stopBrowser sent)')
            except Exception as e:
                logger.warning('Ziniao stopBrowser failed: %s', e)
            finally:
                self._stop_data = None
                self._socket_port = None
