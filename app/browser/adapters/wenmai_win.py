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

import json
import logging
import re

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

    def kill_kernel_by_debug_port(self, port: int, env_id: str) -> bool:
        """Kill THIS store's stuck 稳卖 kernel, and nothing else.

        PowerShell only LISTS the processes whose command line carries
        the debug-port flag; the safety-critical match — all three of
        the port flag, the 稳卖 kernel exe path, and this store's env
        id in ``--user-data-dir`` — runs in :func:`is_wenmai_kernel`,
        pure and unit-tested. Returns True when the kernel was
        identified (the kill itself is best-effort, like every kill);
        False means nothing matched — the caller must NOT keep
        guessing.
        """
        script = (
            '@(Get-CimInstance Win32_Process | Where-Object { '
            f"($_.CommandLine -match '--remote-debugging-port={port}(\\D|$)')"
            ' } | Select-Object ProcessId, ExecutablePath, CommandLine'
            ' | ConvertTo-Json -Compress)'
        )
        out = _run_ps(script).strip()
        try:
            procs = json.loads(out) if out else []
        except ValueError:
            return False
        if isinstance(procs, dict):  # one result is not an array
            procs = [procs]
        victims = [
            int(p['ProcessId'])
            for p in procs
            if isinstance(p, dict)
            and is_wenmai_kernel(
                str(p.get('CommandLine') or ''),
                str(p.get('ExecutablePath') or ''),
                port,
                env_id,
            )
        ]
        if not victims:
            return False
        pid_list = ','.join(str(v) for v in victims)
        _run_ps(
            f'Stop-Process -Id {pid_list} -Force '
            "-ErrorAction SilentlyContinue;'ok'"
        )
        return True


# The live kernel exe runs from the client's Browser tree, wherever the
# client's userData lives (verified: NOT under Program Files — the
# kernel exe is ``<userData>\WenMaiAIBrowser\...\Browser\...
# \WenMaiAIBrowser.exe``). The dir names identify the vendor; another
# app's browser carrying the same debug-port flag fails this check.
_KERNEL_PATH_RE = r'wenmaiaibrowser\\.*\\browser\\'
# The kernel's user-data-dir ends with this store's env id (verified:
# ``<userData>\WenMaiAIBrowser\storeCache\<envId>``).
_UDD_TAIL_RE = r'--user-data-dir=+"?[^"]*\\{env_id}("|\s|$)'


def is_wenmai_kernel(
    cmdline: str, exe_path: str, port: int, env_id: str
) -> bool:
    """Whether a process is THIS store's stuck 稳卖 kernel.

    All three must hold — the debug-port flag, the 稳卖 kernel exe
    path, and the env id at the tail of ``--user-data-dir``. The port
    alone is NOT enough: anything on the machine can carry a
    ``--remote-debugging-port`` (other vendors' kernels, a debug
    Chrome), and a plausible cause of OUR kernel's bind failing is
    that one of them already holds the port — matching on the port
    alone would kill a production store browser of another app.
    """
    lowered = (exe_path or '').replace('/', '\\').lower()
    if not re.search(_KERNEL_PATH_RE, lowered):
        return False
    if not re.search(f'--remote-debugging-port={port}(\\D|$)', cmdline or ''):
        return False
    return bool(
        re.search(_UDD_TAIL_RE.format(env_id=re.escape(env_id)), cmdline or '')
    )
