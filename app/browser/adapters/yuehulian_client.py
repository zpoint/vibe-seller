"""Real Windows-driven Yuehulian client (composes the IO + DOM layers).

Implements :class:`InjectClient` on top of :mod:`yuehulian_win`
(process + inspector) and :mod:`yuehulian_dom` (pure parsing). The read
path (ensure_running / is_logged_in / list_stores) is validated live; the
open_store write path (spawn-hook → click → DevToolsActivePort) is
exercised by the Step 4 e2e. The inspector is opened per operation and
closed right after, so no code-exec surface lingers.
"""

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import time

from app.browser.adapters.yuehulian_dom import (
    LOGIN_HASH_JS,
    SCRAPE_STORES_JS,
    click_open_js,
    is_logged_in_hash,
    parse_store_list_json,
)
from app.browser.adapters.yuehulian_win import InspectorSession, ProcessLayer
from app.browser.inject.client import InjectClient, OpenedStore, StoreRef
from app.browser.inject.errors import (
    CdpUnreachable,
    ClientNotRunning,
    LoginRequired,
    StoreNotFound,
)

logger = logging.getLogger(__name__)

_LOCALHOST = '127.0.0.1'


class RealYuehulianClient(InjectClient):
    """Drives the real 悦互联 Electron client on Windows."""

    def __init__(self, browser_config: dict | None = None):
        cfg = browser_config or {}
        self._proc = ProcessLayer(cfg.get('client_path') or None)

    async def ensure_running(self) -> None:
        if await asyncio.to_thread(self._proc.is_running):
            return
        if not await asyncio.to_thread(self._proc.launch):
            raise ClientNotRunning('could not locate/launch the client')
        for _ in range(30):
            await asyncio.sleep(1.0)
            if await asyncio.to_thread(self._proc.main_pid):
                return
        raise ClientNotRunning('client did not come up after launch')

    @asynccontextmanager
    async def _inspector(self):
        pid = await asyncio.to_thread(self._proc.main_pid)
        if not pid:
            raise ClientNotRunning('no main process to inspect')
        insp = InspectorSession(self._proc)
        await insp.open(pid)
        try:
            yield insp
        finally:
            await insp.close()

    async def is_logged_in(self) -> bool:
        async with self._inspector() as insp:
            route = await insp.eval_in_renderer(LOGIN_HASH_JS)
        return is_logged_in_hash(route)

    async def list_stores(self) -> list[StoreRef]:
        async with self._inspector() as insp:
            route = await insp.eval_in_renderer(LOGIN_HASH_JS)
            if not is_logged_in_hash(route):
                raise LoginRequired()
            rows = await insp.eval_in_renderer(SCRAPE_STORES_JS)
        return parse_store_list_json(json.dumps(rows))

    async def open_store(self, ref: str) -> OpenedStore:
        async with self._inspector() as insp:
            route = await insp.eval_in_renderer(LOGIN_HASH_JS)
            if not is_logged_in_hash(route):
                raise LoginRequired()
            rows = await insp.eval_in_renderer(SCRAPE_STORES_JS)
            names = {s.name for s in parse_store_list_json(json.dumps(rows))}
            if ref not in names:
                raise StoreNotFound(ref)

            await insp.install_spawn_hook()
            try:
                before = len(await insp.recorded_spawns())
                click_ts = time.time()
                result = await insp.eval_in_renderer(click_open_js(ref))
                if not str(result or '').startswith('OK'):
                    raise StoreNotFound(ref)
                udd = await self._await_spawn_udd(insp, before)
            finally:
                await insp.uninstall_spawn_hook()

        port = await self._await_devtools_port(udd, newer_than=click_ts - 5)
        return OpenedStore(_LOCALHOST, port, udd)

    async def close_store(self, ref: str) -> None:
        # Best-effort: the env stays open until the client/user closes it
        # (the engine already tore down the CDPMuxProxy). A clean
        # CDP Browser.close / GUI close is a follow-up.
        return None

    async def _await_spawn_udd(
        self, insp: InspectorSession, before: int
    ) -> str:
        for _ in range(20):
            spawns = await insp.recorded_spawns()
            if len(spawns) > before:
                udd = str(spawns[-1].get('udd') or '').strip()
                if udd:
                    return udd
            await asyncio.sleep(0.5)
        raise CdpUnreachable('no kernel spawn was intercepted')

    async def _await_devtools_port(
        self, user_data_dir: str, *, newer_than: float
    ) -> int:
        for _ in range(24):
            port = await asyncio.to_thread(
                self._proc.read_devtools_port, user_data_dir, newer_than
            )
            if port:
                return port
            await asyncio.sleep(0.5)
        raise CdpUnreachable(f'no fresh DevToolsActivePort in {user_data_dir}')
