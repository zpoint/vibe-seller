"""Workflow coverage for the CDP-proxy port allocation (PR C1).

The unit tier pins the allocator's mechanics (bind-before-use, foreign
listeners skipped); this tier pins the DB-facing contract the user
approved: a proxy port SAVED in the DB that is now held by another
server instance is REALLOCATED, never reused blindly — here for the
store-less web browser's ``app_settings.web_browser_proxy_port`` kv,
the one saved port that survives server restarts.
"""

import json
import socket

import pytest
import websockets

from app.browser.manager import BrowserManager
from app.models.app_settings import AppSettings

pytestmark = pytest.mark.workflow

_WEB_PORT_KEY = 'web_browser_proxy_port'


async def _foreign_listener(instance_id: str) -> int:
    """A listener that answers like ANOTHER server's CDPMuxProxy."""

    async def _answer(connection, request):
        if request.path.split('?', 1)[0] == '/vibe/instance':
            return connection.respond(
                200, json.dumps({'instance': instance_id})
            )
        return None

    server = await websockets.serve(
        lambda ws: None, '127.0.0.1', 0, process_request=_answer
    )
    return server


class TestWebProxyPortKv:
    async def test_a_foreign_saved_port_is_reallocated_and_persisted(
        self, override_async_session
    ):
        mgr = BrowserManager()
        server = await _foreign_listener('another-server')
        held_port = server.sockets[0].getsockname()[1]
        try:
            async with override_async_session() as db:
                db.add(AppSettings(key=_WEB_PORT_KEY, value=str(held_port)))
                await db.commit()

            async with override_async_session() as db:
                new_port = await mgr._web_proxy_port(db)
                assert new_port != held_port
                # The kv now carries the REALLOCATED port, so the next
                # restart re-adopts the port this server actually uses.
                row = await db.get(AppSettings, _WEB_PORT_KEY)
                assert int(row.value) == new_port
        finally:
            server.close()
            await server.wait_closed()

    async def test_a_free_saved_port_is_adopted_unchanged(
        self, override_async_session
    ):
        """Single-instance behaviour unchanged across restarts: a saved
        port nobody took is re-adopted, kv untouched."""
        mgr = BrowserManager()
        # A port nothing listens on: bind-and-close proves it free.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            free_port = s.getsockname()[1]

        async with override_async_session() as db:
            db.add(AppSettings(key=_WEB_PORT_KEY, value=str(free_port)))
            await db.commit()

        async with override_async_session() as db:
            assert await mgr._web_proxy_port(db) == free_port
