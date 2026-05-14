"""
Multi-client CDP WebSocket multiplexing proxy.

Allows multiple browser-use CLI processes to share a single browser
by routing CDP messages based on request IDs and session ownership.

Architecture:
    browser-use CLI (task 1) --ws-->  CDPMuxProxy  --ws-->  Browser
    browser-use CLI (task 2) --ws-->       |
    browser-use CLI (task 3) --ws-->       |

Isolation mechanisms (borrowed from https://github.com/dyyz1993/cdp-tunnel):
  1. Request ID rewriting — each client's msg.id is remapped to a
     global counter so responses route back to the correct client.
  2. Session-based event routing — CDP sessionId maps to the owning
     client; page events only go to the tab owner.
  3. Target filtering — getTargets() responses are stripped of other
     clients' targets; cross-client attachToTarget is blocked.

No browser-context isolation: CDP's Target.createBrowserContext creates
incognito-like contexts with a fresh cookie jar (verified by test), so
tasks share the default profile context to inherit pre-logged-in sessions.

Download path override (``download_dir``):
  macOS sends SIGTERM to the browser-use daemon process after 3-5 min
  of background execution (PPID=1, stdout/stderr=/dev/null, no
  terminal).  This is standard macOS behavior for idle background
  processes with no UI — we cannot prevent it.  Each daemon restart
  creates a fresh BrowserProfile whose model validator generates a
  random ``/tmp/browser-use-downloads-{uuid}/`` directory and calls
  ``Browser.setDownloadBehavior`` with it.  Because setDownloadBehavior
  is browser-wide (last writer wins), the download path changes on
  every restart, and files end up in an unpredictable temp dir.

  Fix: when ``download_dir`` is set, the proxy intercepts every
  ``Browser.setDownloadBehavior`` call from any client and rewrites
  ``downloadPath`` to the stable per-store directory.  This makes
  downloads deterministic regardless of how many times the daemon
  restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import websockets

from app.browser.cdp_mux_routing import _RoutingMixin
from app.browser.cdp_mux_types import ClientState, RequestMapping
from app.browser.cdp_mux_upstream import _UpstreamMixin
from app.config import LOCALHOST

# Re-export for backwards compatibility.
__all__ = ['CDPMuxProxy', 'ClientState', 'RequestMapping']

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_CLIENTS = 5
PENDING_CACHE_TTL = 30.0  # seconds before pending events are discarded
# Grace period before closing a disconnected client's tabs.
# If the same client_id reconnects within this window, it
# recovers its tabs instead of starting from about:blank.
# The browser-use daemon disconnects after ~4 min idle;
# 480s (8 min) gives enough headroom for reconnection.
DEFAULT_CLEANUP_GRACE = 480.0  # seconds


# ---------------------------------------------------------------------------
# CDPMuxProxy
# ---------------------------------------------------------------------------


class CDPMuxProxy(_UpstreamMixin, _RoutingMixin):
    """WebSocket-level CDP multiplexing proxy."""

    def __init__(
        self,
        listen_port: int,
        target_port: int,
        target_host: str = LOCALHOST,
        max_clients: int = DEFAULT_MAX_CLIENTS,
        cleanup_grace: float = DEFAULT_CLEANUP_GRACE,
        download_dir: str | None = None,
    ):
        self.listen_port = listen_port
        self.target_port = target_port
        self.target_host = target_host
        self.max_clients = max_clients
        self.cleanup_grace = cleanup_grace
        self.download_dir = download_dir

        # Upstream connection to browser
        self._upstream: websockets.ClientConnection | None = None
        self._upstream_lock = asyncio.Lock()

        # Downstream clients
        self._clients: dict[str, ClientState] = {}

        # Request ID de-multiplexing
        self._global_id_counter: int = 0
        self._global_request_map: dict[int, RequestMapping] = {}

        # Ownership maps
        self._session_to_client: dict[str, str] = {}
        self._target_to_client: dict[str, str] = {}

        # Race-condition buffers (attachedToTarget before createTarget)
        # Stored as {targetId: (timestamp, msg)} with TTL cleanup.
        self._pending_attached: dict[str, tuple[float, dict]] = {}
        self._pending_created: dict[str, tuple[float, dict]] = {}

        # Server handle
        self._server: websockets.asyncio.server.Server | None = None
        self._upstream_task: asyncio.Task | None = None
        self._running = False
        self._reconnecting = False

        # Deferred cleanup: client_id → (asyncio.Task, ClientState)
        # Holds disconnected clients' state during grace period.
        self._deferred_cleanups: dict[
            str, tuple[asyncio.Task, ClientState]
        ] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the proxy: connect upstream, listen for clients."""
        self._running = True
        await self._connect_upstream_and_cleanup()

        # Disable WebSocket keepalive on the client-facing side. The
        # proxy listens on 127.0.0.1; daemons are local processes. There
        # is no NAT or flaky-network scenario for keepalive to detect,
        # and a daemon waiting between agent commands is silent on the
        # wire — pinging it just produced false-positive disconnects
        # (`code=1011 keepalive ping timeout`) mid-task that triggered
        # destructive `/browser/start` recovery via manager._cdp_alive.
        # TCP-level RST/FIN still detects a genuinely dead daemon
        # (websockets raises ConnectionClosed when the next write/read
        # hits a closed socket).
        self._server = await websockets.serve(
            self._handle_client_ws,
            LOCALHOST,
            self.listen_port,
            process_request=self._process_http,
            ping_interval=None,
            ping_timeout=None,
        )

        logger.info(
            'CDPMuxProxy listening: %s:%d -> %s:%d (max_clients=%d)',
            LOCALHOST,
            self.listen_port,
            self.target_host,
            self.target_port,
            self.max_clients,
        )

    async def stop(self) -> None:
        """Stop proxy and disconnect all clients."""
        self._running = False
        if self._upstream_task:
            self._upstream_task.cancel()
            self._upstream_task = None
        if self._upstream:
            await self._upstream.close()
            self._upstream = None
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._clients.clear()
        self._global_request_map.clear()
        self._session_to_client.clear()
        self._target_to_client.clear()
        self._pending_attached.clear()
        self._pending_created.clear()
        for task, _ in self._deferred_cleanups.values():
            task.cancel()
        self._deferred_cleanups.clear()
        logger.info('CDPMuxProxy stopped (port %d)', self.listen_port)

    def has_active_clients(self) -> bool:
        return bool(self._clients)

    # ------------------------------------------------------------------
    # Client cleanup
    # ------------------------------------------------------------------

    async def _defer_cleanup(self, client_id: str) -> None:
        """Schedule deferred tab cleanup after grace period.

        The browser-use daemon may disconnect briefly (idle timeout,
        reconnect) and come back with the same client_id. Immediate
        cleanup would destroy tabs the daemon still expects to own.
        """
        client = self._clients.pop(client_id, None)
        if not client:
            return

        if not client.target_ids and not client.session_ids:
            # Nothing to preserve — clean up immediately.
            logger.info(
                'CDPMuxProxy client %s disconnected (empty) — '
                'no deferred cleanup needed',
                client_id[:16],
            )
            return

        async def _deferred() -> None:
            try:
                await asyncio.sleep(self.cleanup_grace)
            except asyncio.CancelledError:
                return
            # Guard: client may have reconnected during sleep.
            current = self._deferred_cleanups.get(client_id)
            if current is None or current[0] is not task:
                return
            if client_id in self._clients:
                return
            self._deferred_cleanups.pop(client_id, None)
            await self._cleanup_client_state(client)

        task = asyncio.create_task(_deferred())
        # Cancel any prior deferred cleanup for same id
        old = self._deferred_cleanups.pop(client_id, None)
        if old:
            old[0].cancel()
        self._deferred_cleanups[client_id] = (task, client)
        logger.info(
            'CDPMuxProxy client %s disconnected — deferred '
            'cleanup in %ds (targets=%d, sessions=%d)',
            client_id,
            int(self.cleanup_grace),
            len(client.target_ids),
            len(client.session_ids),
        )

    async def _cleanup_client_state(self, client: ClientState) -> None:
        """Close tabs and remove mappings for a ClientState."""
        client_id = client.client_id

        logger.info(
            'CDPMuxProxy cleaning up client %s (targets=%d, sessions=%d)',
            client_id,
            len(client.target_ids),
            len(client.session_ids),
        )

        # Close client's tabs
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
            self._pending_attached.pop(target_id, None)
            self._pending_created.pop(target_id, None)

        # Remove session mappings
        for session_id in client.session_ids:
            self._session_to_client.pop(session_id, None)

        # Remove pending requests for this client
        stale_gids = [
            gid
            for gid, m in self._global_request_map.items()
            if m.client_id == client_id
        ]
        for gid in stale_gids:
            self._global_request_map.pop(gid, None)

        logger.info(
            'CDPMuxProxy client %s cleaned up (remaining: %d)',
            client_id,
            len(self._clients),
        )

    async def disconnect_client(self, client_id: str) -> None:
        """Force-disconnect a specific client."""
        client = self._clients.get(client_id)
        if client:
            await client.ws.close(4000, 'Disconnected by server')
            # _cleanup_client fires via the handler's finally

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _next_global_id(self) -> int:
        self._global_id_counter += 1
        return self._global_id_counter

    def _sweep_pending_caches(self) -> None:
        """Remove stale entries from pending event caches."""
        now = time.monotonic()
        for cache in (self._pending_attached, self._pending_created):
            stale = [
                k
                for k, (ts, _) in cache.items()
                if now - ts > PENDING_CACHE_TTL
            ]
            for k in stale:
                cache.pop(k, None)

    async def _send_client(self, client: ClientState, msg: dict) -> None:
        """Send a JSON message to a client."""
        try:
            await client.ws.send(json.dumps(msg))
        except websockets.ConnectionClosed:
            pass

    async def _send_client_error(
        self,
        client_id: str,
        request_id: int | None,
        session_id: str | None,
        message: str,
    ) -> None:
        """Send a CDP error response to a client."""
        logger.debug(
            'CDP error -> client %s: %s',
            client_id[:8] if client_id else '?',
            message,
        )
        client = self._clients.get(client_id)
        if not client or request_id is None:
            return
        err: dict[str, Any] = {
            'id': request_id,
            'error': {'code': -32600, 'message': message},
        }
        if session_id:
            err['sessionId'] = session_id
        await self._send_client(client, err)
