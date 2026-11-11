"""One liveness probe for a store's CDP proxy port.

The port is the CDPMuxProxy, a ``websockets`` server whose HTTP layer
REJECTS HTTP/1.0 outright (``unsupported protocol; expected HTTP/1.1``)
and never writes a response. A 1.0 probe therefore reads every healthy
proxy as dead. The aux browser learned that first; the browser manager
kept its own 1.0 copy, so reusing a running store session always
"failed" the probe and force-restarted the proxy — cutting every live
CDP client on that store (a parent agent's browser vanished the moment
its subagent started a worker slot). Both callers use this one function
so the protocol detail lives in exactly one place.
"""

from __future__ import annotations

import asyncio

from app.config import LOCALHOST


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
