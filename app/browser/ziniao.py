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
import uuid

import aiohttp

from app.browser.base import BrowserBackend, BrowserSessionInfo
from app.browser.cdp_mux_proxy import CDPMuxProxy
from app.browser.ziniao_utils import (
    ensure_ziniao_running,
    is_wsl,
    kill_and_relaunch_ziniao,
    try_connect_ziniao,
    ziniao_host,
)
from app.config import DOWNLOADS_DIR, LOCALHOST

logger = logging.getLogger(__name__)


class ZiniaoBackend(BrowserBackend):
    """Start/stop ziniao browser profiles and connect via CDP."""

    def __init__(self):
        self._proxy: CDPMuxProxy | None = None

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

        # After long uptime Ziniao can enter a stale state where
        # startBrowser returns statusCode 0 + a fresh debuggingPort but
        # never actually launches Chrome, so that port is unreachable.
        # A plain stopBrowser/startBrowser retry does NOT clear it (the
        # status IS 0) — only a full client restart does. So we probe the
        # raw port after each startBrowser; on an unreachable port we
        # kill+relaunch Ziniao and retry, bounded to MAX_ZINIAO_ATTEMPTS
        # to avoid an infinite loop, then surface a clear error.
        MAX_ZINIAO_ATTEMPTS = 3
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
                stop_data = {
                    'action': 'stopBrowser',
                    **user_info,
                }
                if browser_oauth.isdigit():
                    stop_data['browserId'] = browser_oauth
                else:
                    stop_data['browserOauth'] = browser_oauth
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
                    f'{MAX_ZINIAO_ATTEMPTS} attempts (including a client '
                    f'restart). The Ziniao client is likely wedged — open '
                    f'it in the GUI and check the environment '
                    f'(proxy / login / quota).'
                )
            # A full client restart clears the stale state; then retry.
            try:
                await kill_and_relaunch_ziniao(
                    socket_port, client_path, user_info
                )
            except Exception as e:
                logger.warning(
                    'kill_and_relaunch_ziniao failed (%s); '
                    'retrying startBrowser anyway',
                    e,
                )
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
        self._proxy = CDPMuxProxy(
            listen_port=proxy_port,
            target_port=int(cdp_port),
            target_host=target_host,
            download_dir=str(dl_dir),
            keep_last_page=True,
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
