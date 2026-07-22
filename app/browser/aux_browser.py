"""Per-store auxiliary browser — a CLEAN, login-less Chromium.

Design (docs/browser.md § Dual browser): every store has exactly two
browsers. The MAIN one (Ziniao for anti-detect stores) carries the
seller account — saved credentials, 2FA auto-fill, the correct IP
environment — but restricts which sites it can open. The AUX one exists
for everything the main browser blocks (public product pages, supplier
sites, search, logistics): an independent Playwright Chromium with its
own per-store profile and NO seller login. It must never be used for
seller-central work.

Lifecycle: LAZY. Nothing starts at boot or at main-browser start; the
store wrapper's ``--session {slug}-aux`` branch calls
``POST /api/stores/{id}/browser/aux/start``, which starts (or returns)
this store's aux Chromium + its own CDPMuxProxy and answers with the
``ws`` endpoint the daemon should attach to. The proxy pins downloads
to ``downloads/{slug}-aux`` and gives the usual multi-client isolation.

Kept separate from BrowserManager: the aux browser has no DB row, no
Ziniao coupling, and no per-task sessions — a module-level registry and
a lock are the whole lifecycle.
"""

from __future__ import annotations

import asyncio
import logging

from app.browser.chrome import ChromeBackend, _free_port
from app.browser.manager import store_slug
from app.config import LOCALHOST
from app.models.store import Store

logger = logging.getLogger(__name__)

_backends: dict[str, ChromeBackend] = {}
_ports: dict[str, int] = {}
_lock = asyncio.Lock()

# Hard bounds so a hung browser op can never hold ``_lock`` — and thus
# wedge aux starts for EVERY store — indefinitely. A healthy cold start
# is a few seconds; a start that can't finish in _START_TIMEOUT is
# wedged, so failing fast (and surfacing it) beats blocking forever.
_START_TIMEOUT = 60.0
_STOP_TIMEOUT = 10.0


def _ws(port: int) -> str:
    return f'ws://{LOCALHOST}:{port}/client-aux'


async def _alive(port: int) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(LOCALHOST, port), timeout=2
        )
        writer.write(
            f'GET /json/version HTTP/1.0\r\nHost: {LOCALHOST}\r\n\r\n'.encode()
        )
        await writer.drain()
        data = await asyncio.wait_for(reader.read(64), timeout=2)
        writer.close()
        return b'200' in data
    except Exception:
        return False


async def start_aux(store: Store, headless: bool) -> dict:
    """Start (or return the running) aux browser for a store."""
    async with _lock:
        port = _ports.get(store.id)
        if port and store.id in _backends and await _alive(port):
            return {'ok': True, 'proxy_port': port, 'ws': _ws(port)}

        # Stale/dead instance — tear down before relaunch. Bounded: a
        # hung teardown must not hold _lock forever. Drop the registry
        # entry up front so a timed-out stop can't leave a dead port
        # advertised.
        old = _backends.pop(store.id, None)
        _ports.pop(store.id, None)
        if old is not None:
            try:
                await asyncio.wait_for(old.stop(), timeout=_STOP_TIMEOUT)
            except Exception:
                logger.warning(
                    'aux stop before relaunch failed/timed out',
                    exc_info=True,
                )

        slug = store_slug(store.name, store.id)
        port = _free_port()
        backend = ChromeBackend()
        try:
            await asyncio.wait_for(
                backend.start({
                    'proxy_port': port,
                    'store_slug': f'{slug}-aux',
                    'headless': headless,
                }),
                timeout=_START_TIMEOUT,
            )
        except Exception:
            # Never register a half-started backend, and never hold the
            # lock past the bound. Best-effort cleanup, then surface the
            # failure so the caller retries instead of hanging.
            try:
                await asyncio.wait_for(backend.stop(), timeout=_STOP_TIMEOUT)
            except Exception:
                logger.debug(
                    'aux cleanup after failed start failed', exc_info=True
                )
            raise
        _backends[store.id] = backend
        _ports[store.id] = port
        logger.info(
            'Aux browser started for store %s (proxy=%d)', store.name, port
        )
        return {'ok': True, 'proxy_port': port, 'ws': _ws(port)}


async def stop_aux(store_id: str) -> bool:
    """Stop a store's aux browser if running. True when one was stopped."""
    async with _lock:
        backend = _backends.pop(store_id, None)
        _ports.pop(store_id, None)
        if backend is None:
            return False
        try:
            await backend.stop()
        except Exception:
            logger.warning('aux browser stop failed', exc_info=True)
        return True


async def stop_all_aux() -> None:
    for store_id in list(_backends):
        await stop_aux(store_id)
