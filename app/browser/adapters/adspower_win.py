"""Windows I/O for the AdsPower (AdsPower Global) client.

Everything here only works on Windows (or WSL-on-Windows, driving the
Windows side over 127.0.0.1). Blocking functions; call via
``asyncio.to_thread``.

Unlike 悦互联/稳卖 there is no GUI injection at all (R2): every flow —
list, open, close — goes through the client's local HTTP API. What
remains Windows-specific:

- install discovery + launching the exe (the GUI; R22 — no headless
  form is ever started);
- reading the ``local_api`` file that names the CURRENT Local API port
  (the port is effectively random: the default 50325 is only used when
  free, and e.g. Hyper-V/WSL port exclusions can make it permanently
  unavailable);
- attributing store-browser kernels (SunBrowser / FlowerBrowser) to a
  profile through their command lines: both kernels put the profile id
  in ``--user-data-dir=<cache>/<profile_id>_<inviteCode>[_flower]``
  (verified live, FACTS.md §m6), so a kernel process tree belonging to
  ONE profile can be found and killed precisely — never a whole image
  name (other profiles may belong to other tasks or to the user).

Validated live 2026-11-27, client v8.9.23 (Electron 35.4.0).
"""

import logging
import os

from app.browser import win_app_locator as win_locator
from app.browser.adapters.adspower_common import (
    USER_DATA_DIR_RE,
    KernelProcess,
    parse_local_api_port_text,
)
from app.browser.win_app_locator import (
    WinAppSpec,
    run_powershell as _run_ps,
)

logger = logging.getLogger(__name__)

_PS = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command']
_UTF8 = '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;'

ADSPOWER_SPEC = WinAppSpec(
    key='adspower',
    display_name='AdsPower',
    exe_names=('AdsPower Global.exe',),
    match_tokens=('adspower',),
    dir_names=('AdsPower Global',),
)

#: The Electron shell's process name (main + helpers, without .exe —
#: Get-Process -Name form).
SHELL_PROCESS_NAME = 'AdsPower Global'

# R6: resolving APPDATA from WSL spawns PowerShell on EVERY discovery
# poll while a client launches; the answer is cached per process.
_APPDATA_PS = "[Environment]::GetFolderPath('ApplicationData')"
_APPDATA_CACHE: dict[str, str] = {}


def windows_appdata() -> str:
    """The Windows ``%APPDATA%`` dir (native env var, or PS from WSL).

    Never hard-codes a user name: under WSL the folder is asked from
    the Windows shell itself. The non-empty result is cached for the
    process lifetime (R6: discovery polls every few seconds while a
    client launches).
    """
    cached = _APPDATA_CACHE.get('v')
    if cached:
        return cached
    if os.name == 'nt':
        value = os.environ.get('APPDATA') or ''
    else:
        value = _run_ps(_APPDATA_PS, timeout=15).strip()
    if value:
        _APPDATA_CACHE['v'] = value
    return value


def local_api_file_path() -> str:
    """The ``local_api`` file's path (Windows-style)."""
    appdata = windows_appdata()
    return (
        f'{appdata}\\adspower_global\\cwd_global\\source\\local_api'
        if appdata
        else ''
    )


def read_local_api_port(to_local=None) -> int | None:
    """The CURRENT Local API port from the ``local_api`` file, or None.

    The file holds a full URL (``http://local.adspower.com:51234/``).
    It can be STALE (a killed client leaves it behind): the caller
    decides liveness via ``GET /status``, never this file alone.
    """
    to_local = to_local or win_locator.win_path_to_local
    path = local_api_file_path()
    if not path:
        return None
    try:
        with open(to_local(path), encoding='utf-8', errors='replace') as fh:
            text = fh.read()
    except OSError:
        return None
    return parse_local_api_port_text(text)


def list_kernel_processes() -> list[KernelProcess]:
    """Every store-browser kernel with its profile id, live.

    One bounded CIM query over the kernel image names (SunBrowser /
    FlowerBrowser / flowerbrowser — the Firefox kernel reports both
    spellings across versions); each hit's ``--user-data-dir`` names
    the profile it belongs to. Injected in tests (the client takes
    this as a seam).
    """
    script = (
        'Get-CimInstance Win32_Process | Where-Object { '
        "$_.Name -eq 'SunBrowser.exe' -or "
        "$_.Name -eq 'FlowerBrowser.exe' -or "
        "$_.Name -eq 'flowerbrowser.exe' } | ForEach-Object { "
        '"$($_.ProcessId)`t$($_.Name)`t$($_.CommandLine)" }'
    )
    out: list[KernelProcess] = []
    for line in _run_ps(script, timeout=30).splitlines():
        parts = line.split('\t')
        if len(parts) != 3:
            continue
        pid_s, name, cmdline = parts
        match = USER_DATA_DIR_RE.search(cmdline or '')
        if not match:
            continue
        try:
            out.append(
                KernelProcess(int(pid_s), name.strip(), match.group('dir'))
            )
        except ValueError:
            continue
    return out


def kill_process_tree(pid: int) -> bool:
    """``taskkill /T /F`` ONE pid (a profile's kernel, never an image).

    ``/T`` also ends the content processes that reference the parent.
    """
    return (
        _run_ps(f'taskkill /T /F /PID {int(pid)}', timeout=30)
        .strip()
        .startswith('SUCCESS')
    )


class AdsPowerProcessLayer:
    """Locate / launch the AdsPower client; attribute its kernels."""

    def __init__(self, install_dir: str | None = None):
        self._dir = install_dir
        #: Why discovery failed (surfaced in ClientNotRunning errors).
        self.locate_error: str | None = None

    def exe_path(self) -> str | None:
        """The client's main exe (Windows path), or None + locate_error.

        A caller-configured path is honoured first (a full ``.exe`` is
        trusted as-is, a dir resolves to the main exe inside it), then
        the shared locator's hints — so any install location works,
        exactly like 悦互联 (PR #298).
        """
        located = win_locator.locate(ADSPOWER_SPEC, configured_path=self._dir)
        if located:
            return located.exe_path
        self.locate_error = win_locator.not_found_message(ADSPOWER_SPEC)
        return None

    def launch(self) -> bool:
        """Start the GUI client (remember-me signs the account in)."""
        exe = self.exe_path()
        if not exe:
            return False
        esc = exe.replace("'", "''")
        script = f"Start-Process -FilePath '{esc}';'ok'"
        return _run_ps(script).strip().endswith('ok')

    def shell_running(self) -> bool:
        """Whether any AdsPower shell process exists (signed in or not).

        With a signed-out GUI the shell runs but the Local API does
        NOT listen (FACTS.md §m1) — that pair is the positive
        'up but not signed in' evidence ``ensure_running`` acts on.
        """
        script = (
            "(Get-Process -Name 'AdsPower Global'"
            ' -ErrorAction SilentlyContinue | Measure-Object).Count'
        )
        out = _run_ps(script, timeout=15).strip()
        try:
            return int(out) > 0
        except ValueError:
            return False


def make_process_layer(install_dir: str | None = None) -> AdsPowerProcessLayer:
    """The module's process layer (uniform surface with adspower_mac)."""
    return AdsPowerProcessLayer(install_dir)
