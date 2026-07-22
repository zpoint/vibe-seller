"""Client/event routing mixin for CDPMuxProxy."""

from __future__ import annotations

import json
import logging
import time
from typing import Any
import uuid

import websockets
from websockets.asyncio.server import ServerConnection

from app.browser.cdp_mux_types import ClientState, RequestMapping
from app.env_options import Options

logger = logging.getLogger(__name__)


class _RoutingMixin:
    """Methods for routing client↔upstream CDP messages."""

    async def _handle_client_ws(self, ws: ServerConnection) -> None:
        """Handle a new downstream client connection."""
        # Extract client ID from path: /client-{id} or fallback
        path = ws.request.path if ws.request else ''
        client_id = self._extract_client_id(path)

        if len(self._clients) >= self.max_clients:
            logger.warning(
                'CDPMuxProxy rejecting client %s: max_clients=%d reached',
                client_id,
                self.max_clients,
            )
            await ws.close(
                4029,
                f'Max clients ({self.max_clients}) reached',
            )
            return

        # Cancel deferred cleanup if same client_id reconnects.
        deferred = self._deferred_cleanups.pop(client_id, None)
        if deferred:
            task, old_state = deferred
            task.cancel()
            # Sessions are CDP attachments owned by the previous
            # daemon process — they cannot be re-used by a new
            # WebSocket connection by spec, and trying to do so
            # poisons BrowserStartEvent on the reconnecting daemon
            # (it tries to use stale sessions, the watchdog times
            # out after 30s, the daemon restarts, inherits even
            # more stale sessions, and enters a death spiral). The
            # earlier per-target gate (`if target_ids:`) was too
            # narrow: after `pkill && open` (the documented
            # mechanics §6 recovery), Chrome keeps the tabs alive
            # so target_ids > 0, but the sessions still belong to
            # the dead daemon. Drop sessions unconditionally; the
            # new daemon issues fresh `Target.attachToTarget` for
            # the targets it actually wants. Targets are owned by
            # Chrome and are still safe to recover.
            if old_state.session_ids:
                logger.info(
                    'CDPMuxProxy client %s: discarding %d stale '
                    'session(s) on reconnect (sessions are owned '
                    'by the prior daemon process)',
                    client_id[:16],
                    len(old_state.session_ids),
                )
                for sid in old_state.session_ids:
                    self._session_to_client.pop(sid, None)
            logger.info(
                'CDPMuxProxy client %s reconnected — recovered %d '
                'target(s), 0 session(s)',
                client_id,
                len(old_state.target_ids),
            )
            client = ClientState(
                client_id=client_id,
                ws=ws,
                target_ids=old_state.target_ids,
                session_ids=set(),
                target_order=[
                    t
                    for t in old_state.target_order
                    if t in old_state.target_ids
                ],
            )
        else:
            client = ClientState(client_id=client_id, ws=ws)

        self._clients[client_id] = client
        self.mark_activity()
        logger.info(
            'CDPMuxProxy client connected: %s (total: %d)',
            client_id,
            len(self._clients),
        )

        try:
            async for raw in ws:
                try:
                    await self._route_client_message(client_id, raw)
                except Exception:
                    logger.exception(
                        'Error routing client %s message',
                        client_id,
                    )
        except websockets.ConnectionClosed as e:
            logger.info(
                'CDPMuxProxy client %s WS closed (exception): '
                'code=%s reason=%s',
                client_id[:16],
                e.code,
                e.reason or '(none)',
            )
        finally:
            # Log close code for clean closes too (no exception)
            code = getattr(ws, 'close_code', None)
            reason = getattr(ws, 'close_reason', None)
            logger.info(
                'CDPMuxProxy client %s WS final: code=%s reason=%s',
                client_id[:16],
                code,
                reason or '(none)',
            )
            await self._defer_cleanup(client_id)

    @staticmethod
    def _extract_client_id(path: str) -> str:
        """Extract client ID from WS path or generate one."""
        # /client-{task_id} or /devtools/browser/{id}
        if path.startswith('/client-'):
            return path[len('/client-') :]
        return str(uuid.uuid4())[:8]

    # ------------------------------------------------------------------
    # Client → Upstream routing
    # ------------------------------------------------------------------

    async def _route_client_message(
        self, client_id: str, raw: str | bytes
    ) -> None:
        """Process a message from a client, forward upstream."""
        self.mark_activity()
        msg = json.loads(raw)
        method = msg.get('method', '')
        original_id = msg.get('id')

        # --- Intercept Browser.close ---
        if method == 'Browser.close':
            await self._handle_browser_close(client_id, msg)
            return

        # --- Rewrite Browser.setDownloadBehavior downloadPath ---
        # browser-use creates a fresh random /tmp dir per CLI
        # invocation.  Override with a stable per-store directory
        # so agents can find downloaded files.
        if method == 'Browser.setDownloadBehavior' and self.download_dir:
            params = msg.get('params', {})
            if 'downloadPath' in params:
                params['downloadPath'] = self.download_dir

        # --- Block cross-client Target.closeTarget ---
        if method == 'Target.closeTarget':
            target_id = msg.get('params', {}).get('targetId')
            owner = self._target_to_client.get(target_id)
            if owner and owner != client_id:
                await self._send_client_error(
                    client_id,
                    original_id,
                    msg.get('sessionId'),
                    'Target owned by another client',
                )
                return

        # --- Block cross-client Target.attachToTarget ---
        if method == 'Target.attachToTarget':
            target_id = msg.get('params', {}).get('targetId')
            owner = self._target_to_client.get(target_id)
            if owner and owner != client_id:
                await self._send_client_error(
                    client_id,
                    original_id,
                    msg.get('sessionId'),
                    'Target owned by another client',
                )
                return

        # --- Rewrite request ID ---
        if original_id is not None:
            global_id = self._next_global_id()
            mapping = RequestMapping(
                client_id=client_id,
                original_id=original_id,
                session_id=msg.get('sessionId'),
            )

            if method == 'Target.createTarget':
                mapping.is_create_target = True
                logger.debug(
                    'CDP %s -> createTarget (client=%s, gid=%d)',
                    msg.get('params', {}).get('url', ''),
                    client_id[:8],
                    global_id,
                )
            if method == 'Target.attachToTarget':
                mapping.is_attach_target = True
                logger.debug(
                    'CDP attachToTarget target=%s (client=%s)',
                    msg.get('params', {}).get('targetId', '')[:16],
                    client_id[:8],
                )

            self._global_request_map[global_id] = mapping
            msg['id'] = global_id

        await self._send_upstream(msg)

    async def _handle_browser_close(self, client_id: str, msg: dict) -> None:
        """Intercept Browser.close: clean up client, don't close."""
        logger.info(
            'CDPMuxProxy: Browser.close from %s (intercepted)',
            client_id,
        )
        # Send mock success response
        client = self._clients.get(client_id)
        if not client:
            return

        # Close client's targets and clean up routing state
        for target_id in list(client.target_ids):
            try:
                gid = self._next_global_id()
                await self._send_upstream({
                    'id': gid,
                    'method': 'Target.closeTarget',
                    'params': {'targetId': target_id},
                })
            except Exception:
                pass
            self._target_to_client.pop(target_id, None)
        client.target_ids.clear()

        for session_id in client.session_ids:
            self._session_to_client.pop(session_id, None)
        client.session_ids.clear()

        # Send mock success response
        resp: dict[str, Any] = {
            'id': msg.get('id'),
            'result': {},
        }
        if msg.get('sessionId'):
            resp['sessionId'] = msg['sessionId']
        await self._send_client(client, resp)

    # ------------------------------------------------------------------
    # Upstream → Client routing
    # ------------------------------------------------------------------

    async def _route_upstream_message(self, raw: str | bytes) -> None:
        """Route a browser message to the correct client(s)."""
        self._sweep_pending_caches()
        msg = json.loads(raw)

        # --- Priority 1: Response with id ---
        if 'id' in msg:
            await self._route_response(msg)
            return

        # --- Priority 2: Session-scoped event ---
        session_id = msg.get('sessionId')
        if session_id:
            owner_id = self._session_to_client.get(session_id)
            if owner_id:
                client = self._clients.get(owner_id)
                if client:
                    await self._send_client(client, msg)
            return

        # --- Priority 3: Root-level events ---
        method = msg.get('method', '')
        if method.startswith('Browser.download'):
            # Download events don't have sessionId but carry
            # a guid.  Route to ALL connected clients — the
            # daemon that initiated the download will recognise
            # its own guid and ignore irrelevant ones.
            for client in list(self._clients.values()):
                try:
                    await self._send_client(client, msg)
                except Exception:
                    pass
        elif method.startswith('Target.'):
            await self._route_target_event(msg)

    async def _route_response(self, msg: dict) -> None:
        """Route a response (has id) to the originating client."""
        global_id = msg['id']
        mapping = self._global_request_map.pop(global_id, None)
        if not mapping:
            return

        client = self._clients.get(mapping.client_id)
        if not client:
            return

        # Restore original request ID
        msg['id'] = mapping.original_id
        if mapping.session_id and 'sessionId' not in msg:
            msg['sessionId'] = mapping.session_id

        # --- Special: createTarget response ---
        if mapping.is_create_target:
            target_id = msg.get('result', {}).get('targetId')
            error = msg.get('error')
            if error:
                logger.debug(
                    'CDP createTarget FAILED client=%s: %s',
                    mapping.client_id[:8],
                    error,
                )
            elif target_id:
                self._target_to_client[target_id] = mapping.client_id
                client.target_ids.add(target_id)
                client.target_order.append(target_id)
                logger.debug(
                    'CDP target %s -> client %s',
                    target_id[:16],
                    mapping.client_id[:8],
                )
                await self._enforce_tab_cap(client)
                # Replay cached attachedToTarget event
                cached_entry = self._pending_attached.pop(target_id, None)
                if cached_entry:
                    _, cached_msg = cached_entry
                    self._map_session_from_attached(
                        cached_msg, mapping.client_id, client
                    )
                    await self._send_client(client, cached_msg)
                # Replay cached targetCreated event
                created_entry = self._pending_created.pop(target_id, None)
                if created_entry:
                    _, created_msg = created_entry
                    await self._send_client(client, created_msg)

        # --- Special: attachToTarget response ---
        if mapping.is_attach_target:
            session_id = msg.get('result', {}).get('sessionId')
            error = msg.get('error')
            if error:
                logger.debug(
                    'CDP attachToTarget FAILED client=%s: %s',
                    mapping.client_id[:8],
                    error,
                )
            elif session_id:
                self._session_to_client[session_id] = mapping.client_id
                client.session_ids.add(session_id)
                logger.debug(
                    'CDP session %s -> client %s (attach)',
                    session_id[:16],
                    mapping.client_id[:8],
                )

        # --- Special: getTargets response (filter) ---
        result = msg.get('result', {})
        if 'targetInfos' in result:
            msg['result']['targetInfos'] = self._filter_targets(
                result['targetInfos'], mapping.client_id
            )

        await self._send_client(client, msg)

    async def _enforce_tab_cap(self, client: ClientState) -> None:
        """Close the client's OLDEST tabs beyond ``VIBE_TAB_CAP``.

        Every navigation is a ``new_tab`` (the only primitive the
        skills use), so a long-running task accumulates one tab per
        step and nothing in-session ever closes one — the browser
        window ends up with hundreds of dead tabs. LRU-close by
        creation order, per client only (ownership isolation means
        this can never touch another task's or a human's tabs). A
        closed old tab an agent later switches back to yields a
        normal CDP target-not-found error it recovers from — the
        same failure mode as a crashed tab, and far cheaper than
        unbounded accumulation. 0 disables.
        """
        cap = Options.TAB_CAP.get_int()
        if cap <= 0:
            return
        while len(client.target_order) > cap:
            oldest = client.target_order.pop(0)
            if oldest not in client.target_ids:
                continue
            client.target_ids.discard(oldest)
            self._target_to_client.pop(oldest, None)
            logger.info(
                'CDPMuxProxy tab cap (%d): closing oldest tab %s of client %s',
                cap,
                oldest[:16],
                client.client_id[:8],
            )
            # Fire-and-forget upstream close: no request mapping is
            # registered, so the response is dropped by design; the
            # browser's targetDestroyed event does the bookkeeping
            # for any other observer.
            await self._send_upstream({
                'id': self._next_global_id(),
                'method': 'Target.closeTarget',
                'params': {'targetId': oldest},
            })

    def _filter_targets(
        self,
        targets: list[dict],
        client_id: str,
    ) -> list[dict]:
        """Filter target list: only show client's own targets.

        Unowned targets (e.g. Chrome's initial about:blank) are hidden
        to prevent contention when multiple clients start simultaneously.
        Each client creates its own pages via Target.createTarget.
        """
        filtered = []
        for t in targets:
            tid = t.get('targetId', '')
            owner = self._target_to_client.get(tid)
            if owner == client_id:
                filtered.append(t)
        return filtered

    async def _route_target_event(self, msg: dict) -> None:
        """Route root-level Target.* events by ownership."""
        # Target events fire on tab creation/navigation/destruction —
        # including HUMAN use of the window — so they count as
        # activity for the idle-browser sweeper.
        self.mark_activity()
        method = msg.get('method', '')
        params = msg.get('params', {})

        if method == 'Target.attachedToTarget':
            await self._handle_attached_to_target(msg)
        elif method == 'Target.detachedFromTarget':
            self._handle_detached_from_target(params)
        elif method == 'Target.targetCreated':
            await self._handle_target_created(msg)
        elif method == 'Target.targetDestroyed':
            await self._handle_target_destroyed(msg)
        elif method == 'Target.targetInfoChanged':
            await self._handle_target_info_changed(msg)

    async def _handle_attached_to_target(self, msg: dict) -> None:
        """Route attachedToTarget: match by targetId or cache."""
        params = msg.get('params', {})
        target_info = params.get('targetInfo', {})
        target_id = target_info.get('targetId', '')

        owner_id = self._target_to_client.get(target_id)
        if owner_id:
            client = self._clients.get(owner_id)
            if client:
                self._map_session_from_attached(msg, owner_id, client)
                await self._send_client(client, msg)
        else:
            # Race: createTarget response hasn't arrived yet
            logger.debug(
                'Caching attachedToTarget for target %s',
                target_id,
            )
            self._pending_attached[target_id] = (time.monotonic(), msg)

    def _map_session_from_attached(
        self,
        msg: dict,
        client_id: str,
        client: ClientState,
    ) -> None:
        """Extract sessionId from attachedToTarget params."""
        session_id = msg.get('params', {}).get('sessionId')
        if session_id:
            self._session_to_client[session_id] = client_id
            client.session_ids.add(session_id)
            logger.debug(
                'Session %s -> client %s (auto-attach)',
                session_id,
                client_id,
            )

    def _handle_detached_from_target(self, params: dict) -> None:
        """Clean up session mapping on detach."""
        session_id = params.get('sessionId')
        if session_id:
            owner_id = self._session_to_client.pop(session_id, None)
            if owner_id:
                client = self._clients.get(owner_id)
                if client:
                    client.session_ids.discard(session_id)

    async def _handle_target_created(self, msg: dict) -> None:
        """Route targetCreated to owner or cache."""
        params = msg.get('params', {})
        target_info = params.get('targetInfo', {})
        target_id = target_info.get('targetId', '')

        owner_id = self._target_to_client.get(target_id)
        if owner_id:
            client = self._clients.get(owner_id)
            if client:
                await self._send_client(client, msg)
        else:
            # May precede createTarget response
            self._pending_created[target_id] = (time.monotonic(), msg)

    async def _handle_target_destroyed(self, msg: dict) -> None:
        """Route targetDestroyed and clean up."""
        params = msg.get('params', {})
        target_id = params.get('targetId', '')

        logger.debug(
            'CDP targetDestroyed %s (owner=%s)',
            target_id[:16],
            self._target_to_client.get(target_id, 'none')[:8]
            if self._target_to_client.get(target_id)
            else 'none',
        )

        owner_id = self._target_to_client.pop(target_id, None)
        self._pending_attached.pop(target_id, None)
        self._pending_created.pop(target_id, None)

        if owner_id:
            client = self._clients.get(owner_id)
            if client:
                client.target_ids.discard(target_id)
                if target_id in client.target_order:
                    client.target_order.remove(target_id)
                await self._send_client(client, msg)

    async def _handle_target_info_changed(self, msg: dict) -> None:
        """Route targetInfoChanged to owner only."""
        params = msg.get('params', {})
        target_info = params.get('targetInfo', {})
        target_id = target_info.get('targetId', '')

        owner_id = self._target_to_client.get(target_id)
        if owner_id:
            client = self._clients.get(owner_id)
            if client:
                await self._send_client(client, msg)
