"""Windows I/O for the Wenmai (稳卖) client (process location + launch).

Install discovery is shared with Ziniao / 悦互联 in
:mod:`app.browser.win_app_locator` (``WENMAI_SPEC``); the Node
inspector machinery is shared in :mod:`app.browser.win_inspector`.
Everything here only works on Windows (or WSL-on-Windows, driving the
Windows side over 127.0.0.1).

Unlike 悦互联, the Wenmai client runs a local Express API on a fixed
port (default 3980) on every normal launch — no special mode, no
kill/relaunch — so process state is derived from that port: the MAIN
process is the owner of the 3980 listener. Validated live 2026-11-27,
client v5.1.0715. All functions are blocking; call via
``asyncio.to_thread``.
"""

import logging

from app.browser import win_app_locator as win_locator
from app.browser.win_app_locator import (
    WinAppSpec,
    run_powershell as _run_ps,
)

logger = logging.getLogger(__name__)

# The client's local HTTP API port (docs + verified live). If 3980 is
# busy the client silently falls back to a random port — out of scope
# here; the common install lands on 3980.
DEFAULT_API_PORT = 3980

# Install discovery for THIS vendor, declared here (not in the shared
# locator) per R1 — vendor knowledge lives in the vendor module. The
# locator itself is generic: it takes any spec.
WENMAI_SPEC = WinAppSpec(
    key='wenmai',
    display_name='稳卖 (WenMai)',
    exe_names=('wenmaiaibrowser.exe',),
    match_tokens=('wenmai', 'wenmaiaibrowser', '稳卖'),
    dir_names=('WenMaiAIBrowser', '稳卖'),
)


class WenmaiProcessLayer:
    """Locate / launch the 稳卖 client and find its main process."""

    def __init__(self, install_dir: str | None = None):
        self._dir = install_dir
        #: Why discovery failed (surfaced in ClientNotRunning errors).
        self.locate_error: str | None = None

    def exe_path(self) -> str | None:
        """The client's main exe (Windows path), or None + locate_error.

        A caller-configured path is honoured first (a full ``.exe`` is
        trusted as-is, a dir resolves to the main exe inside it), then
        the shared locator's hints (registry, shortcuts, conventional
        dirs) — so any install location works, exactly like 悦互联.
        The spec is THIS module's WENMAI_SPEC (vendor knowledge stays
        in the vendor module; the locator itself is generic).
        """
        located = win_locator.locate(WENMAI_SPEC, configured_path=self._dir)
        if located:
            return located.exe_path
        self.locate_error = win_locator.not_found_message(WENMAI_SPEC)
        return None

    def exe_path_script(self) -> str:
        """PS preamble assigning the main exe to ``$exe`` (inspector)."""
        exe = self.exe_path() or (
            r'C:\Program Files\WenMaiAIBrowser\WenMaiAIBrowser.exe'
        )
        esc = exe.replace("'", "''")
        return f"$exe='{esc}';"

    def launch(self) -> bool:
        exe = self.exe_path()
        if not exe:
            return False
        esc = exe.replace("'", "''")
        script = f"Start-Process -FilePath '{esc}';'ok'"
        return _run_ps(script).strip().endswith('ok')

    def main_pid(self, api_port: int = DEFAULT_API_PORT) -> int | None:
        """The MAIN process pid: the owner of the local API listener.

        The API runs on every normal launch, so its listener owner IS
        the Electron main process (a CURRENT pid — a stale one fails
        the inspector open with an error).
        """
        script = (
            f'(Get-NetTCPConnection -LocalPort {api_port} -State Listen '
            '-ErrorAction SilentlyContinue).OwningProcess'
        )
        out = _run_ps(script).strip()
        for tok in out.split():
            try:
                return int(tok)
            except ValueError:
                continue
        return None

    def kill_kernel_by_debug_port(self, port: int) -> bool:
        """Kill the store kernel whose command line carries this port.

        A kernel whose ``--remote-debugging-port`` bind silently failed
        (seen live: the client had picked a port in a Windows reserved
        range, so the kernel RUNS but its endpoint never answers, and
        closeEnv does not clear it) is matched by its OWN debug-port
        flag and stopped — never by name, so the client, other stores'
        kernels and unrelated processes are untouched. Returns True
        when the PowerShell ran (best-effort, like every kill).
        """
        script = (
            'Get-CimInstance Win32_Process | '
            'Where-Object { $_.CommandLine -match '
            f"'--remote-debugging-port={port}(\\D|$)' }} | "
            'ForEach-Object { Stop-Process -Id $_.ProcessId -Force '
            "-ErrorAction SilentlyContinue };'ok'; exit 0"
        )
        # exit 0: a stopped-for-already-gone pid must not read as a
        # failed kill (the match itself is the contract).
        return _run_ps(script).strip().endswith('ok')
