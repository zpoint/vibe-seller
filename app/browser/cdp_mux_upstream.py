"""Upstream-facing mixin for CDPMuxProxy (browser connection + HTTP)."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
import json
import logging
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import aiohttp
import websockets
from websockets.asyncio.server import ServerConnection
from websockets.http11 import Request, Response

from app.config import LOCALHOST

logger = logging.getLogger(__name__)


class _UpstreamMixin:
    """Methods for connecting to the upstream browser and HTTP shim."""

    async def _connect_upstream_and_cleanup(self) -> None:
        """Connect upstream, run cleanup, then start read loop.

        Cleanup must run BEFORE the read loop starts because
        websockets doesn't allow concurrent recv() calls.
        """
        ws_url = await self._discover_ws_url()
        logger.debug('CDPMuxProxy connecting upstream: %s', ws_url)
        self._upstream = await websockets.connect(
            ws_url,
            ping_interval=30,
            ping_timeout=30,
            max_size=50 * 1024 * 1024,  # 50 MB for screencast
        )
        logger.info('CDPMuxProxy upstream connected: %s', ws_url)
        await self._startup_cleanup()
        await self._startup_download_behavior()
        self._upstream_task = asyncio.create_task(self._read_upstream())

    async def _connect_upstream(self) -> None:
        """Re-establish upstream WebSocket (reconnect path)."""
        ws_url = await self._discover_ws_url()
        self._upstream = await websockets.connect(
            ws_url,
            ping_interval=30,
            ping_timeout=30,
            max_size=50 * 1024 * 1024,
        )
        await self._startup_download_behavior()
        self._upstream_task = asyncio.create_task(self._read_upstream())

    async def _discover_ws_url(self) -> str:
        """GET /json/version on the target to find wsDebuggerUrl."""
        url = f'http://{self.target_host}:{self.target_port}'
        async with aiohttp.ClientSession() as session:
            async with session.get(f'{url}/json/version') as resp:
                data = await resp.json()
                return data['webSocketDebuggerUrl']

    async def _read_upstream(self) -> None:
        """Read loop: route browser messages to correct client(s)."""
        try:
            async for raw in self._upstream:
                try:
                    await self._route_upstream_message(raw)
                except Exception:
                    logger.exception('Error routing upstream message')
        except websockets.ConnectionClosed:
            logger.warning('CDPMuxProxy upstream disconnected')
            await self._handle_upstream_disconnect()
        except asyncio.CancelledError:
            pass

    async def _send_upstream(self, msg: dict) -> None:
        """Send a CDP message to the browser."""
        if self._upstream:
            await self._upstream.send(json.dumps(msg))

    async def _handle_upstream_disconnect(self) -> None:
        """Send error responses for pending requests, clear state."""
        for gid, mapping in list(self._global_request_map.items()):
            client = self._clients.get(mapping.client_id)
            if client:
                err = {
                    'id': mapping.original_id,
                    'error': {
                        'code': -32603,
                        'message': 'Browser disconnected',
                    },
                }
                if mapping.session_id:
                    err['sessionId'] = mapping.session_id
                await self._send_client(client, err)
        self._global_request_map.clear()
        self._session_to_client.clear()
        self._target_to_client.clear()
        self._pending_attached.clear()
        self._pending_created.clear()

        # Attempt reconnect (single-flight: skip if already in progress)
        if self._running and not self._reconnecting:
            self._reconnecting = True
            asyncio.create_task(self._reconnect_upstream())

    async def _reconnect_upstream(self) -> None:
        """Reconnect with exponential backoff (max 10 attempts)."""
        delay = 1.0
        max_delay = 30.0
        max_attempts = 10
        try:
            for attempt in range(1, max_attempts + 1):
                if not self._running:
                    return
                try:
                    logger.info(
                        'CDPMuxProxy reconnecting in %.1fs... (attempt %d/%d)',
                        delay,
                        attempt,
                        max_attempts,
                    )
                    await asyncio.sleep(delay)
                    await self._connect_upstream()
                    logger.info('CDPMuxProxy upstream reconnected')
                    return
                except Exception as e:
                    logger.warning('CDPMuxProxy reconnect failed: %s', e)
                    delay = min(delay * 2, max_delay)
            # Reconnecting to the CURRENT upstream is hopeless: the browser
            # is gone or its debugging port rotated (classic after a Ziniao
            # restart — the old port serves nothing). Before giving up (and
            # 502-ing every /json/version forever), self-heal by relaunching
            # the upstream browser and reconnecting to its FRESH port.
            if self._running and await self._relaunch_and_reconnect_upstream():
                return
            logger.error(
                'CDPMuxProxy gave up reconnecting after %d attempts',
                max_attempts,
            )
            asyncio.create_task(self.stop())
        finally:
            self._reconnecting = False

    async def _relaunch_and_reconnect_upstream(self) -> bool:
        """Self-heal: relaunch the upstream browser, reconnect to its
        fresh port. Returns True iff the proxy is healthy again.

        The injected ``relaunch_upstream`` hook re-launches the backing
        browser (e.g. a fresh Ziniao ``startBrowser`` after a client
        restart) and returns its new ``(port, host)`` — or just a port,
        or None if it cannot recover. On success we repoint the proxy at
        the new target and re-establish the upstream WebSocket, so
        re-dispatched tasks get a live browser instead of a 502.
        """
        relaunch = getattr(self, '_relaunch_upstream', None)
        if relaunch is None:
            return False
        try:
            fresh = await relaunch()
        except Exception as e:
            logger.warning('CDPMuxProxy upstream relaunch failed: %s', e)
            return False
        if not fresh:
            return False
        if isinstance(fresh, tuple):
            new_port, new_host = int(fresh[0]), fresh[1]
        else:
            new_port, new_host = int(fresh), self.target_host
        logger.info(
            'CDPMuxProxy self-heal: relaunched upstream, repointing '
            '%s:%d -> %s:%d',
            self.target_host,
            self.target_port,
            new_host,
            new_port,
        )
        self.target_host = new_host
        self.target_port = new_port
        try:
            await self._connect_upstream()
        except Exception as e:
            logger.warning('CDPMuxProxy reconnect after relaunch failed: %s', e)
            return False
        logger.info(
            'CDPMuxProxy upstream self-healed on %s:%d',
            self.target_host,
            self.target_port,
        )
        return True

    # ------------------------------------------------------------------
    # Startup cleanup
    # ------------------------------------------------------------------

    async def _startup_cleanup(self) -> None:
        """Close orphan web-page tabs left by a prior crash.

        Only closes ``http://`` / ``https://`` pages.  Extension,
        devtools, and internal chrome pages are left alone.

        Uses a recv-loop that matches on the request ``id`` so
        unsolicited CDP events arriving before the response don't
        get silently consumed.

        When ``keep_last_page`` is True (Ziniao), one page is always
        left open: closing the final page closes the whole browser
        window, which tears down the Ziniao environment (its env opens
        on a single launcher page). Extra orphan tabs are still reaped.
        """
        try:
            gid = self._next_global_id()
            await self._send_upstream({
                'id': gid,
                'method': 'Target.getTargets',
            })
            # Read until we get OUR response; discard stray events.
            targets: list[dict] = []
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                raw = await asyncio.wait_for(
                    self._upstream.recv(),
                    timeout=max(remaining, 0.1),
                )
                msg = json.loads(raw)
                if msg.get('id') == gid:
                    targets = msg.get('result', {}).get('targetInfos', [])
                    break
                # else: unsolicited event — discard

            # Only actual web pages (http/https). Extension, devtools,
            # chrome:// pages are kept.
            pages = [
                t
                for t in targets
                if t.get('type') == 'page'
                and t.get('url', '').startswith(('http://', 'https://'))
            ]
            # Never close the env's last page — closing the final page
            # closes the browser window (fatal for Ziniao). Reap extras.
            if getattr(self, 'keep_last_page', False) and pages:
                logger.info(
                    'CDPMuxProxy startup: keeping last page open: %s',
                    pages[-1].get('url', '')[:80],
                )
                pages = pages[:-1]

            closed = 0
            for t in pages:
                logger.info(
                    'CDPMuxProxy startup: closing orphan tab: %s (target=%s)',
                    t.get('url', '')[:80],
                    t['targetId'],
                )
                cid = self._next_global_id()
                await self._send_upstream({
                    'id': cid,
                    'method': 'Target.closeTarget',
                    'params': {'targetId': t['targetId']},
                })
                closed += 1
            if closed:
                logger.info(
                    'CDPMuxProxy startup: closed %d orphan tab(s)',
                    closed,
                )
        except Exception as e:
            logger.warning('CDPMuxProxy startup cleanup failed: %s', e)

    async def _startup_download_behavior(self) -> None:
        """Configure Chrome's download path proactively on upstream connect.

        Without this, we rely on browser-use's DownloadsWatchdog to call
        ``Browser.setDownloadBehavior`` — but that only fires on the
        first ``Target.attachedToTarget`` event after the daemon starts.
        Clicks that trigger downloads *before* that event land in
        Chrome's last-configured / default directory (for Ziniao that's
        ``~/Library/Application Support/ziniaobrowserdatas/ziniao
        browser/{slug}/``) instead of the per-store
        ``~/.vibe-seller/downloads/{slug}/`` we expect.

        Reproduction: before this fix, about half of the downloads from
        a fresh daemon landed in Ziniao's native dir and the other half
        in the proxy-rewritten dir, with the transition happening the
        first time the watchdog fired — observed 2026-04-24 on a
        demo-northshore store while downloading FBA tax invoice PDFs.

        Sending this from the proxy on every upstream connect (startup
        AND reconnect) closes the race: Chrome has the per-store path
        before any client can trigger a download.

        Implementation: fire-and-forget. We must not read from
        ``self._upstream`` here because ``_read_upstream`` hasn't
        started yet — a recv loop would steal unsolicited browser
        events and (on the reconnect path) responses to still-in-flight
        client requests. Chrome's reply to this ``id`` lands in
        ``_read_upstream`` once it starts and is dropped by
        ``_route_response`` since no mapping exists for our ``gid``;
        that's fine. If the call is ever rejected we only find out via
        downloads not landing — but ``Browser.setDownloadBehavior``
        with ``behavior=allow`` and a pre-mkdir'd path is well-
        exercised in production and unlikely to fail.
        """
        if not self.download_dir:
            return
        try:
            gid = self._next_global_id()
            await self._send_upstream({
                'id': gid,
                'method': 'Browser.setDownloadBehavior',
                'params': {
                    'behavior': 'allow',
                    'downloadPath': self.download_dir,
                    'eventsEnabled': True,
                },
            })
            logger.info(
                'CDPMuxProxy startup: pinning download path to %s '
                '(fire-and-forget, gid=%d)',
                self.download_dir,
                gid,
            )
        except Exception as e:
            logger.warning(
                'CDPMuxProxy startup setDownloadBehavior send failed: %s',
                e,
            )

    # ------------------------------------------------------------------
    # Wedged-tab recovery
    # ------------------------------------------------------------------

    async def reset_orphan_page_tabs(
        self, requesting_client: str | None = None
    ) -> int:
        """Close orphan ``http(s)`` page tabs to recover a wedged tab.

        A page renderer can hang — unresponsive to the CDP
        ``Page``/``Runtime`` startup handshake — while the browser
        *process* stays alive.  When that happens ``/json/version``
        keeps returning 200 (it is a browser-level endpoint), so the
        per-store browser looks healthy, but a freshly-spawned
        browser-use daemon's ``BrowserStartEvent`` times out attaching
        to the wedged tab and every ``open`` retry fails identically.
        This is exactly the failure mode that stranded one store’s
        run (CDP "connection timeout" was a symptom, not the cause).

        Closing the orphan web tab(s) lets the next ``open`` create a
        clean tab.  ``Target.closeTarget`` is handled by the browser
        process and kills the renderer even when the page is hung.

        Scoping: the proxy serves multiple concurrent tasks per store,
        so this must never close a sibling task's healthy tabs.  A tab
        is closed only if it is *unowned* (a true orphan — e.g. a tab
        the browser opened itself, or a leftover from a crash; these
        never went through proxy ``Target.createTarget``) or owned by
        ``requesting_client``.  The requester's own wedged tab stays
        matchable because ``_target_to_client`` entries survive the
        daemon's death through the deferred-cleanup grace window.
        Tabs owned by any *other* client — connected or in grace —
        are skipped.

        Fire-and-forget: the responses arrive in ``_read_upstream`` and
        are dropped (no request mapping for our gid), so this is safe to
        call while the proxy read loop is running.  The target list is
        fetched over the independent HTTP ``/json/list`` shim, never by
        racing the WebSocket recv loop.

        Returns the number of tabs closed.
        """
        data = await self._fetch_upstream_http('/json/list')
        if not data:
            return 0
        closed = 0
        skipped = 0
        for t in data:
            if t.get('type') != 'page':
                continue
            url = t.get('url', '')
            if not url.startswith(('http://', 'https://')):
                continue
            target_id = t.get('id') or t.get('targetId')
            if not target_id:
                continue
            owner = self._target_to_client.get(target_id)
            if owner is not None and owner != requesting_client:
                # Owned by another task (live or in its reconnect
                # grace window) — closing it would break isolation.
                skipped += 1
                continue
            try:
                gid = self._next_global_id()
                await self._send_upstream({
                    'id': gid,
                    'method': 'Target.closeTarget',
                    'params': {'targetId': target_id},
                })
                # Drop any client ownership so the next daemon starts clean.
                self._target_to_client.pop(target_id, None)
                self._pending_attached.pop(target_id, None)
                self._pending_created.pop(target_id, None)
                if owner is not None:
                    # Also drop the id from the owner's ClientState so
                    # a reconnect doesn't "recover" a dead target.
                    live = self._clients.get(owner)
                    if live:
                        live.target_ids.discard(target_id)
                    deferred = self._deferred_cleanups.get(owner)
                    if deferred:
                        deferred[1].target_ids.discard(target_id)
                closed += 1
                logger.info(
                    'CDPMuxProxy reset: closing wedged tab %s (target=%s)',
                    url[:80],
                    target_id,
                )
            except Exception:
                logger.warning(
                    'CDPMuxProxy reset: closeTarget failed', exc_info=True
                )
        logger.info(
            'CDPMuxProxy reset: closed %d orphan page tab(s), '
            'skipped %d owned by other clients (requester=%s)',
            closed,
            skipped,
            requesting_client or '<none>',
        )
        return closed

    # ------------------------------------------------------------------
    # HTTP handler (for /json/version, /json/list, /vibe/reset-tabs)
    # ------------------------------------------------------------------

    async def _process_http(
        self,
        connection: ServerConnection,
        request: Request,
    ) -> Response | None:
        """Handle HTTP requests for CDP discovery endpoints."""
        path = request.path

        if path == '/json/version' or path == '/json':
            data = await self._fetch_upstream_http('/json/version')
            if data:
                # Rewrite wsDebuggerUrl to point at proxy
                browser_id = data.get('webSocketDebuggerUrl', '').rsplit(
                    '/', 1
                )[-1]
                data['webSocketDebuggerUrl'] = (
                    f'ws://{LOCALHOST}:{self.listen_port}'
                    f'/devtools/browser/{browser_id}'
                )
                body = json.dumps(data).encode()
                return Response(
                    HTTPStatus.OK,
                    'OK',
                    websockets.Headers({
                        'Content-Type': 'application/json',
                        'Content-Length': str(len(body)),
                    }),
                    body,
                )
            return Response(
                HTTPStatus.BAD_GATEWAY, 'Bad Gateway', websockets.Headers(), b''
            )

        if path.split('?', 1)[0] == '/vibe/reset-tabs':
            # Wedged-tab recovery hook for the wrapper's self-healing
            # ``open``.  Closes orphan web tabs so the next daemon
            # attaches to a clean tab instead of hanging on a dead one.
            # ``?client=<id>`` scopes the reset to that client's tabs
            # (plus true orphans) so sibling tasks are never touched.
            query = parse_qs(urlsplit(path).query)
            requesting = (query.get('client') or [''])[0] or None
            closed = await self.reset_orphan_page_tabs(requesting)
            body = json.dumps({'closed': closed}).encode()
            return Response(
                HTTPStatus.OK,
                'OK',
                websockets.Headers({
                    'Content-Type': 'application/json',
                    'Content-Length': str(len(body)),
                }),
                body,
            )

        if path == '/json/list':
            data = await self._fetch_upstream_http('/json/list')
            if data is not None:
                body = json.dumps(data).encode()
                return Response(
                    HTTPStatus.OK,
                    'OK',
                    websockets.Headers({
                        'Content-Type': 'application/json',
                        'Content-Length': str(len(body)),
                    }),
                    body,
                )
            return Response(
                HTTPStatus.BAD_GATEWAY, 'Bad Gateway', websockets.Headers(), b''
            )

        # Not an HTTP endpoint — let WebSocket upgrade proceed
        return None

    async def _fetch_upstream_http(self, path: str) -> Any:
        """Fetch an HTTP endpoint from the browser."""
        url = f'http://{self.target_host}:{self.target_port}{path}'
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    return await resp.json()
        except Exception as e:
            logger.warning('CDPMuxProxy fetch %s failed: %s', url, e)
            return None
