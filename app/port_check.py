"""Say so, loudly, when this server is not on the port it tells its agents.

THE INVARIANT: the port everything calls back on (``BACKEND_PORT``, handed
to the in-task MCP, the browser-use wrappers and the workspace assistant)
is the port this server actually serves. `vibe-seller start` and start.sh
set it, but any other launch (a bare ``python -m uvicorn … --port N``, a
systemd unit) can leave it at its default. Then every in-task MCP tool
fails with a bare "All connection attempts failed" and nothing says why.

This middleware turns that into ONE error line on the first request: it
compares the port the request arrived on (``scope['server']``, the socket
uvicorn is listening on) with ``BACKEND_PORT`` and logs the fix. It never
blocks a request. Hosts that are not IP literals (test transports such as
``testserver``) carry no listening socket and are skipped.
"""

import ipaddress
import logging

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


def mismatch_message(serving: int, configured: int) -> str:
    return (
        f'BACKEND_PORT={configured} but this server listens on {serving}: '
        'every in-task MCP tool and browser wrapper will call the wrong '
        'port ("All connection attempts failed"). Start it with '
        f'`vibe-seller start --port {serving}`, or set BACKEND_PORT='
        f'{serving} in its environment.'
    )


def _serving_port(scope: Scope) -> int | None:
    server = scope.get('server')
    if not server or server[1] is None:
        return None
    try:
        ipaddress.ip_address(str(server[0]).strip('[]'))
    except ValueError:
        return None
    return int(server[1])


class PortCheckMiddleware:
    """Log one ERROR when the serving port differs from BACKEND_PORT."""

    def __init__(self, app: ASGIApp, configured_port: int) -> None:
        self.app = app
        self.configured_port = configured_port
        self.checked = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if not self.checked and scope['type'] == 'http':
            serving = _serving_port(scope)
            if serving is not None:
                self.checked = True
                if serving != self.configured_port:
                    logger.error(
                        mismatch_message(serving, self.configured_port)
                    )
        await self.app(scope, receive, send)
