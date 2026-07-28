"""The aux-browser liveness probe must speak HTTP/1.1.

The port it probes is the CDPMuxProxy, whose HTTP layer is h11, and h11
rejects HTTP/1.0 outright — so a 1.0 probe can never observe a 200 and
`_alive` returned False for a healthy browser every time, making the
caller's "reuse the running instance" branch unreachable.
"""

import asyncio

import pytest

from app.browser import aux_browser


async def _serve(strict_http11: bool):
    """Tiny server that answers 200 only for an HTTP/1.1 request line."""
    seen = {}

    async def handle(reader, writer):
        line = await reader.readline()
        seen['line'] = line.decode(errors='replace').strip()
        ok = b'HTTP/1.1' in line
        if strict_http11 and not ok:
            writer.close()  # h11's behaviour: no response at all
            return
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}')
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(handle, aux_browser.LOCALHOST, 0)
    return srv, srv.sockets[0].getsockname()[1], seen


@pytest.mark.unit
@pytest.mark.asyncio
async def test_probe_uses_http11_and_sees_alive():
    srv, port, seen = await _serve(strict_http11=True)
    try:
        alive = await aux_browser._alive(port)
    finally:
        srv.close()
    assert 'HTTP/1.1' in seen.get('line', ''), seen
    assert alive is True, 'a healthy h11 proxy must read as alive'


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dead_port_reads_as_dead():
    assert await aux_browser._alive(1) is False
