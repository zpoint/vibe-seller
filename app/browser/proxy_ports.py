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
from app.config import DEMO_MODE, LOCALHOST

logger = logging.getLogger(__name__)

# How far past the current counter the allocator walks looking for a
# free port before giving up (a listener squatting on every candidate
# is a machine problem to surface, not to loop over forever).
MAX_PORT_WALK = 64

#: Per-instance CDP proxy port BASE (user decision 2026-11-27): lets
#: parallel test servers on one machine run in SEPARATE port ranges
#: instead of queueing for one 9222 holder. Unset = the historical
#: default (invisible to users); set = this server's ports start there.
BASE_ENV = 'VIBE_CDP_PROXY_BASE'


def default_proxy_base() -> int:
    """The historical default base: 9322 in demo mode, else 9222."""
    return 9322 if DEMO_MODE else 9222


def resolve_proxy_base() -> int:
    """This server's CDP proxy port base.

    ``VIBE_CDP_PROXY_BASE`` (1024–65000) wins when set and valid; a
    garbage or out-of-range value logs a warning and falls back to the
    default (:func:`default_proxy_base`) — a typo must never take a
    server down. The allocator walks UP from the result, so a server
    never hands out a port below its base.
    """
    default = default_proxy_base()
    raw = os.environ.get(BASE_ENV, '').strip()
    if not raw:
        return default
    try:
        base = int(raw)
    except ValueError:
        logger.warning(
            '%s=%r is not an integer — using the default proxy base %d',
            BASE_ENV,
            raw,
            default,
        )
        return default
    if not 1024 <= base <= 65000:
        logger.warning(
            '%s=%d is outside 1024–65000 — using the default proxy base %d',
            BASE_ENV,
            base,
            default,
        )
        return default
    return base


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
        """Bind (LOCALHOST, port) WITHOUT listening; None when taken.

        This IS the port check — probing a port free and binding it
        later is a TOCTOU race two concurrent servers would keep
        losing.

        No ``listen()`` until the handoff: a listening reservation
        accepts the wrappers' ownership probes into its backlog, where
        they get no answer, time out and close — and the mux then
        accepts the dead connection and logs one ``opening handshake
        failed`` per store start (found in 84's final regression).
        Bound-but-not-listening instead gives every probe a prompt
        ECONNREFUSED, and the mux calls ``listen()`` itself right
        before ``websockets.serve`` takes the socket over.

        No ``SO_REUSEADDR`` either — that is what keeps the
        allocation atomic without the listen. On Linux, two sockets
        that BOTH set SO_REUSEADDR can bind the same port while
        neither listens (the silent double-bind / port-hijack class);
        with ours off, a second bind of the port fails with
        EADDRINUSE no matter what the other socket sets — on Linux,
        macOS and Windows alike. The cost: a port with connections in
        TIME_WAIT (a mux that just stopped) cannot be re-reserved for
        ~60 s, so the allocator walks to the next port — the typed
        ``proxy_port`` follow in the wrapper already handles that.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((LOCALHOST, port))
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
