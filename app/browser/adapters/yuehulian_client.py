"""Real Windows-driven Yuehulian client (filled in Step 2).

Validated mechanism (see the design doc):
  * locate install via registry / Start Menu; launch via Start-Process.
  * login state: route hash ``#/index`` (logged in) vs login route —
    read through the renderer, NOT ``input[type=password]`` (a hidden
    login form exists even when logged in).
  * list / open stores: open a Node inspector into the Electron MAIN
    process (``ELECTRON_RUN_AS_NODE`` + ``process._debugProcess(pid)``),
    then ``BrowserWindow.webContents.executeJavaScript`` to read the
    el-table and click a row's Open button (UIA is a dead end — a11y is
    off). The kernel launches with no debug port, so a spawn hook adds
    ``--remote-debugging-port=0``; read the port from the store's
    ``DevToolsActivePort``.

Until Step 2 lands, the methods raise so a misconfig surfaces clearly
rather than silently; all CI coverage runs against ``FakeYuehulianClient``.
"""

from app.browser.inject.client import InjectClient, OpenedStore, StoreRef
from app.browser.inject.errors import ClientNotRunning


class RealYuehulianClient(InjectClient):
    """Drives the real 悦互联 Electron client on Windows (Step 2)."""

    def __init__(self, browser_config: dict):
        self._cfg = browser_config

    async def ensure_running(self) -> None:
        raise ClientNotRunning(
            'RealYuehulianClient not implemented yet (Step 2)'
        )

    async def is_logged_in(self) -> bool:
        raise NotImplementedError('Step 2')

    async def list_stores(self) -> list[StoreRef]:
        raise NotImplementedError('Step 2')

    async def open_store(self, ref: str) -> OpenedStore:
        raise NotImplementedError('Step 2')

    async def close_store(self, ref: str) -> None:
        return None
