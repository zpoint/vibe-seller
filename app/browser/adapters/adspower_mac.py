"""macOS I/O for the AdsPower (AdsPower Global) client.

The macOS twin of :mod:`app.browser.adapters.adspower_win`; the
platform-specific pieces of the vendor live behind it, picked in ONE
place (``_platform_layer_module`` in :mod:`app.browser.adapters
.adspower`). The local-API client itself is platform-neutral.

Like the Windows side there is no GUI injection (R2): every flow goes
through the client's local HTTP API or its documented command-line
flags. What remains macOS-specific:

- install discovery + launching, through the SHARED
  :mod:`app.browser.mac_app_locator` (PR #330): the GUI via the
  locator's ``launch`` (``open``), headless by EXECUTING the bundle's
  inner exe directly and detached — ``open --args`` only passes args
  when the app is not already running, and headless is started
  precisely when the GUI shell IS running (sitting at its login page);
- the ``local_api`` file (the port discovery source) under
  ``~/Library/Application Support`` — the layout is the vendor's
  Linux/Windows one, confirmed for macOS in §B;
- kernel process evidence for one profile id (D7) through
  ``psutil``: the store-browser kernels live OUTSIDE the app bundle
  (under the data dir) and name the profile in
  ``--user-data-dir=<cache>/<profile_id>_<inviteCode>[_flower]``, the
  same as on Windows — the ``_flower`` suffix is what marks the
  Firefox kernel cross-platform (vendor source).

Everything here is blocking; call via ``asyncio.to_thread``. The
kernel image NAMES are macOS-unverified until §B; the matching keys on
the user-data-dir, not the process name, so an unexpected name cannot
misattribute a tree.

NOTE: ``'mac'`` is NOT yet in ``ADSPOWER_VENDOR_INFO['platforms']`` —
it joins in the same commit that records the §B live pass.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess

import psutil

from app.browser import mac_app_locator as mac_locator
from app.browser.adapters.adspower_common import (
    KernelProcess,
    parse_local_api_port_text,
)
from app.browser.mac_app_locator import MacAppSpec

logger = logging.getLogger(__name__)

#: The macOS twin of the shared ``USER_DATA_DIR_RE`` — tolerant of
#: SPACES in the path. This layer matches ONE argv element at a time
#: (psutil's ``cmdline`` is a list, so a space cannot cross an
#: argument boundary), and the real macOS kernel path carries one
#: (``…/Library/Application Support/adspower_global/…/cache/<id>_<…>``
#: — verified live on macOS, client 8.9.23). The shared pattern's
#: ``\S*`` is load-bearing only for the Windows twin, whose input is
#: a JOINED command line; applied here it matched nothing, leaving
#: the kernel evidence (and with it the D7 orphan retry and the
#: FlowerBrowser cleanup) blind on macOS.
MAC_USER_DATA_DIR_RE = re.compile(
    r'--user-data-dir=(?P<dir>.*[/\\]cache[/\\](?P<id>[^\s/\\]+))',
    re.IGNORECASE,
)

#: Install discovery for THIS vendor on macOS (R1: vendor knowledge
#: lives in the vendor module). The bundle id is the vendor's
#: convention for the Global edition — VERIFIED against the installed
#: app's Info.plist in §B (ADDENDUM-2 §A).
ADSPOWER_MAC_APP = MacAppSpec(
    key='adspower',
    display_name='AdsPower',
    bundle_ids=('com.adspower.global',),
    app_names=('AdsPower Global.app',),
)

#: The local_api file (the CURRENT Local API port source). Same layout
#: as the vendor's Linux/Windows trees — confirmed live in §B.
LOCAL_API_RELATIVE_PATH = (
    'Library/Application Support/adspower_global/cwd_global/source/local_api'
)


def local_api_file_path() -> str:
    """The ``local_api`` file's path (macOS layout, under $HOME)."""
    return os.path.expanduser(f'~/{LOCAL_API_RELATIVE_PATH}')


def read_local_api_port() -> int | None:
    """The CURRENT Local API port from the ``local_api`` file.

    Same contract as the Windows twin: only the port is taken from the
    URL, and the file can be stale — the caller decides liveness via
    ``GET /status``.
    """
    try:
        with open(
            local_api_file_path(), encoding='utf-8', errors='replace'
        ) as fh:
            text = fh.read()
    except OSError:
        return None

    return parse_local_api_port_text(text)


def _cmdline(proc: psutil.Process) -> list[str]:
    try:
        return proc.as_dict(['cmdline']).get('cmdline') or []
    except psutil.Error:
        return []


def list_kernel_processes() -> list[KernelProcess]:
    """Every store-browser kernel with its profile id, via psutil.

    The kernels run OUTSIDE the app bundle (under the data dir) and
    carry the profile in ``--user-data-dir`` — the same convention as
    Windows (verified against the vendor source; confirmed live in
    §B). Name-agnostic on purpose: attribution comes from the
    user-data-dir, never the process name.
    """
    out: list[KernelProcess] = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        info = proc.info
        cmdline = info.get('cmdline') or []
        for arg in cmdline:
            match = MAC_USER_DATA_DIR_RE.search(arg)
            if match:
                out.append(
                    KernelProcess(
                        info.get('pid') or proc.pid,
                        str(info.get('name') or ''),
                        match.group('dir'),
                    )
                )
                break
    return out


def kill_process_tree(pid: int) -> bool:
    """Kill ONE profile's kernel tree (psutil; never a whole image)."""
    try:
        parent = psutil.Process(int(pid))
        children = parent.children(recursive=True)
    except psutil.Error:
        return False
    ok = True
    for proc in [*children, parent]:
        try:
            proc.kill()
        except psutil.Error:
            ok = False
    psutil.wait_procs([*children, parent], timeout=3)
    return ok


class MacAdsPowerLayer:
    """Locate / launch the AdsPower client on macOS; same duck-typed
    surface as the Windows :class:`AdsPowerProcessLayer`."""

    def __init__(self, install_dir: str | None = None):
        self._saved = install_dir
        #: Why discovery failed (surfaced in ClientNotRunning errors).
        self.locate_error: str | None = None

    def _locate(self):
        located = mac_locator.locate(ADSPOWER_MAC_APP, saved_path=self._saved)
        if located is None:
            self.locate_error = mac_locator.not_found_message(ADSPOWER_MAC_APP)
        return located

    def exe_path(self) -> str | None:
        """The bundle's inner executable, or None + locate_error."""
        located = self._locate()
        return located.exe_path if located else None

    def launch(self) -> bool:
        """Start the GUI client (remember-me signs the account in)."""
        located = self._locate()
        if located is None:
            return False
        try:
            mac_locator.launch(located)
        except RuntimeError as e:
            logger.warning('adspower GUI launch failed: %s', e)
            return False
        return True

    def launch_headless(self, api_key: str) -> bool:
        """Start a headless instance that logs in with the API key.

        The bundle's inner exe is executed DIRECTLY and detached
        (``start_new_session=True``): ``open --args`` drops its flags
        when the app is already running, and headless is started
        precisely when the GUI shell is (sitting at its login page) —
        the two coexist, exactly like the Windows side (FACTS §d).
        The key is only ever in the argv (the vendor's own design,
        documented); it never appears in a log line or an error.
        """
        exe = self.exe_path()
        if not exe or not api_key:
            return False
        try:
            subprocess.Popen(
                [
                    exe,
                    '--headless=true',
                    f'--api-key={api_key}',
                ],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except OSError as e:
            # Type + errno only — the argv carries the key.
            logger.warning(
                'adspower headless launch failed (%s)', type(e).__name__
            )
            return False
        return True

    def shell_running(self) -> bool:
        """Whether any AdsPower bundle process exists (any sign-in)."""
        located = self._locate()
        if located is None:
            return False
        return bool(mac_locator.app_processes(located))


def make_process_layer(
    install_dir: str | None = None,
) -> MacAdsPowerLayer:
    """The module's process layer (uniform surface with adspower_win)."""
    return MacAdsPowerLayer(install_dir)
