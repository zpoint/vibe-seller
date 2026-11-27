"""Bind-before-use CDP-proxy port allocation (PR C1).

Two vibe-seller servers on one machine used to share CDP proxy ports:
the allocator trusted its own counter and the DB, and the wrapper's
liveness probe accepted whatever answered — a test task on one server
drove another server's store browser (on the production Mac that could
be production's Ziniao browser). This module makes the hand-out atomic
with the bind:

- every port the allocator hands out is BOUND (and held) at allocation
  time, and the store's CDPMuxProxy later serves on that very socket
  (``handoff``) — two servers allocating "the same free port" at the
  same moment cannot both win; one bind fails with EADDRINUSE and that
  server walks on;
- a port already held by anything else — another server instance's
  proxy (a DIFFERENT instance id), an agent-spawned CDP bridge
  squatting in the range, or any unrelated listener — is skipped;
- a port persisted in the DB (``browser_sessions.proxy_port``,
  ``app_settings.web_browser_proxy_port``) is re-adopted only when the
  bind proves it free, so a port that became foreign while this server
  was down is reallocated, never reused blindly;
- on a single-instance machine (every real user today) the behaviour is
  unchanged: with no foreign listeners the first store still gets the
  base port and each next store the next one.
"""

from __future__ import annotations

import logging
import os
import socket

from app.browser.cdp_probe import FOREIGN, proxy_ownership
from app.config import LOCALHOST

logger = logging.getLogger(__name__)

# How far past the current counter the allocator walks looking for a
# free port before giving up (a listener squatting on every candidate
# is a machine problem to surface, not to loop over forever).
MAX_PORT_WALK = 64


class ProxyPortAllocator:
    """Per-server-instance proxy-port reservations, keyed by store."""

    def __init__(self, base_port: int, instance_id: str):
        self.instance_id = instance_id
        # store_id (or the web-browser slug) -> proxy port
        self.ports: dict[str, int] = {}
        # Bind-before-use reservations: port -> listening socket, held
        # from allocation until the store's mux serves on it (handoff)
        # or the allocation is torn down (release/pop). A reserved port
        # answers no HTTP, so every ownership probe — ours and other
        # servers' — correctly reads it as not-yet-ours.
        self.sockets: dict[int, socket.socket] = {}
        self.next_port = base_port

    # ── Reservation primitives ──────────────────────────

    @staticmethod
    def _reserve_socket(port: int) -> socket.socket | None:
        """Bind + listen on (LOCALHOST, port); None when it is taken.

        This IS the port check — probing a port free and binding it
        later is a TOCTOU race two concurrent servers would keep
        losing.

        SO_REUSEADDR is set on POSIX only, matching what asyncio's
        ``create_server`` does there, so a reservation taken over by
        the mux and the mux's own restart behave the same. On Windows
        SO_REUSEADDR permits DOUBLE BINDS (silent port hijack), so it
        is never set there — EADDRINUSE stays truthful.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == 'posix':
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((LOCALHOST, port))
            sock.listen(16)
            return sock
        except OSError:
            sock.close()
            return None

    def release(self, port: int | None) -> None:
        """Close this manager's reservation for *port*, if any left.

        No-op once the port's mux has taken the socket over (the
        reservation was popped at handoff). Closing here only fires
        for ports whose browser never came up — exactly the listener
        that must not linger.
        """
        if port is None:
            return
        sock = self.sockets.pop(port, None)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def handoff(self, port: int) -> socket.socket | None:
        """The reservation socket for *port*, if still ours to serve.

        Returns the socket WITHOUT closing it — the mux serves on it
        and closes it via its own ``stop()``. The socket is dropped
        from the reservation map here, so a later ``release`` of the
        same port cannot close the mux's listener.
        """
        return self.sockets.pop(port, None)

    # ── Lookup / teardown ───────────────────────────────

    def get(self, key: str) -> int | None:
        return self.ports.get(key)

    def pop(self, key: str) -> int | None:
        """Forget *key*'s allocation and free its reservation."""
        port = self.ports.pop(key, None)
        self.release(port)
        return port

    # ── Allocation ──────────────────────────────────────

    async def allocate(self, key: str, existing_port: int | None = None) -> int:
        """Reserve (by binding) a unique proxy port for *key*.

        ``existing_port`` is the port persisted for this browser in the
        DB; it is re-adopted only when the bind proves it free
        (server-restart reuse, as before). A bind failure means someone
        holds it; the ownership probe says who, for the log, and the
        allocator walks on to a fresh port.
        """
        if key in self.ports:
            return self.ports[key]
        taken = set(self.ports.values())
        if existing_port is not None and existing_port not in taken:
            sock = self._reserve_socket(existing_port)
            if sock is not None:
                self.ports[key] = existing_port
                self.sockets[existing_port] = sock
                # Keep next_port above any restored port
                if existing_port >= self.next_port:
                    self.next_port = existing_port + 1
                return existing_port
            state = await proxy_ownership(existing_port, self.instance_id)
            logger.warning(
                'Proxy port %d saved for %s is held by a %s listener — '
                'reallocating',
                existing_port,
                key,
                'foreign' if state == FOREIGN else 'stale',
            )
        for _ in range(MAX_PORT_WALK):
            port = self.next_port
            self.next_port += 1
            if port in taken:
                continue
            sock = self._reserve_socket(port)
            if sock is not None:
                self.ports[key] = port
                self.sockets[port] = sock
                return port
            # Bind failed: another server's proxy, an agent-spawned
            # bridge, or one of our own untracked listeners. Either way
            # this port is not ours to hand out.
            logger.info(
                'Proxy port %d is already in use — skipping (candidate for %s)',
                port,
                key,
            )
        raise RuntimeError(
            f'No free CDP proxy port within {MAX_PORT_WALK} candidates '
            f'from the base — something is squatting on the proxy port '
            'range'
        )
