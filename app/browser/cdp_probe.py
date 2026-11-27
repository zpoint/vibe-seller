"""Liveness + ownership probes for a store's CDP proxy port.

The port is the CDPMuxProxy, a ``websockets`` server whose HTTP layer
REJECTS HTTP/1.0 outright (``unsupported protocol; expected HTTP/1.1``)
and never writes a response. A 1.0 probe therefore reads every healthy
proxy as dead. The aux browser learned that first; the browser manager
kept its own 1.0 copy, so reusing a running store session always
"failed" the probe and force-restarted the proxy — cutting every live
CDP client on that store (a parent agent's browser vanished the moment
its subagent started a worker slot). Both callers use this one function
so the protocol detail lives in exactly one place.

:func:`proxy_ownership` answers the harder question — WHO owns the
listener — by asking the proxy's ``/vibe/instance`` handshake endpoint
(PR C1). Liveness alone was the cross-server bug: two vibe-seller
servers on one machine allocate from the same base port, and a probe
that accepts any 200-answer glued server B's wrapper onto server A's
browser. The tri-state is deliberately fail-safe: anything that does
not positively answer with OUR instance id (another server's proxy, an
agent-spawned CDP bridge, a vendor's local API, a listener that accepts
but never answers) reads as ``'foreign'``.
"""

from __future__ import annotations

import asyncio
import json

from app.config import LOCALHOST

#: ``proxy_ownership`` results.
FREE = 'free'
OURS = 'ours'
FOREIGN = 'foreign'


async def cdp_proxy_alive(port: int, timeout: float = 2.0) -> bool:
    """True when ``GET /json/version`` on *port* answers 200."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(LOCALHOST, port), timeout=timeout
        )
    except Exception:
        return False
    try:
        # HTTP/1.1 with an explicit close: 1.0 is rejected by the proxy,
        # and 1.1 keeps the connection open unless told otherwise.
        writer.write(
            f'GET /json/version HTTP/1.1\r\n'
            f'Host: {LOCALHOST}:{port}\r\nConnection: close\r\n\r\n'.encode()
        )
        await writer.drain()
        data = await asyncio.wait_for(reader.read(256), timeout=timeout)
        return b'200' in data[:50]
    except Exception:
        return False
    finally:
        writer.close()


async def proxy_ownership(
    port: int, instance_id: str, timeout: float = 1.0
) -> str:
    """Who listens on *port*: ``'free'`` | ``'ours'`` | ``'foreign'``.

    - ``'free'`` — nothing accepts the connection (the normal state of
      a port this server may allocate or re-adopt after a restart).
    - ``'ours'`` — a listener answers ``GET /vibe/instance`` with
      *instance_id*: a CDPMuxProxy started by THIS server instance.
    - ``'foreign'`` — a listener is there but does not positively
      identify as ours: another server instance's proxy, an
      agent-spawned CDP bridge, any unrelated HTTP server, or a socket
      that accepts and then stays silent. Never attach to it.

    Raw-socket HTTP like :func:`cdp_proxy_alive` (not aiohttp): the
    probe runs under the manager's launch lock on the event loop, and
    must stay allocation-free and cancellation-safe.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(LOCALHOST, port), timeout=timeout
        )
    except ConnectionRefusedError:
        return FREE
    except Exception:
        # Connect timeout / reset: something odd holds the port. Treat
        # it as occupied — handing it out would fail the bind anyway.
        return FOREIGN
    try:
        writer.write(
            f'GET /vibe/instance HTTP/1.1\r\n'
            f'Host: {LOCALHOST}:{port}\r\nConnection: close\r\n\r\n'.encode()
        )
        await writer.drain()
        buf = b''
        # Read the response head (+ body). The mux answers with one
        # small write, so a couple of bounded reads suffice; a listener
        # that never answers runs into the timeout and reads foreign.
        while b'\r\n\r\n' not in buf and len(buf) < 8192:
            chunk = await asyncio.wait_for(reader.read(512), timeout=timeout)
            if not chunk:
                break
            buf += chunk
        head, _, body = buf.partition(b'\r\n\r\n')
        # The body may trail the head by a read; complete it per
        # Content-Length when the header is present.
        for line in head.split(b'\r\n'):
            if line.lower().startswith(b'content-length:'):
                try:
                    want = int(line.split(b':', 1)[1].strip())
                except ValueError:
                    break
                while len(body) < want:
                    chunk = await asyncio.wait_for(
                        reader.read(want - len(body)), timeout=timeout
                    )
                    if not chunk:
                        break
                    body += chunk
                break
        if b' 200 ' not in head.split(b'\r\n', 1)[0][:32]:
            return FOREIGN
        try:
            served = json.loads(body.decode()).get('instance')
        except Exception:
            return FOREIGN
        return OURS if served == instance_id else FOREIGN
    except Exception:
        return FOREIGN
    finally:
        writer.close()
