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

        logger.info(
            'Starting ziniao browser (oauth=%s, port=%d)',
            browser_oauth,
            socket_port,
        )
        # Use try_connect_ziniao to handle both mirrored (127.0.0.1)
        # and NAT (gateway IP) modes on WSL.
        result, host_used = await try_connect_ziniao(
            socket_port, start_data, timeout=60
        )
        if not result or str(result.get('statusCode')) != '0':
            # startBrowser can fail if Chrome was killed but
            # Ziniao control app is still alive (stale browser
            # state).  Try stopBrowser to clean up, then retry.
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
            # Retry startBrowser
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

        logger.info('Ziniao browser started, CDP port: %s', cdp_port)

        # Resolve the host where Ziniao's CDP is listening.
        # If we connected via 127.0.0.1 (mirrored mode), use that.
        # Otherwise fall back to the gateway IP (NAT mode).
        if host_used == LOCALHOST:
            target_host = LOCALHOST
        else:
            target_host = ziniao_host()

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
        self._proxy = CDPMuxProxy(
            listen_port=proxy_port,
            target_port=int(cdp_port),
            target_host=target_host,
            download_dir=str(dl_dir),
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
