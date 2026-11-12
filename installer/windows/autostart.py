"""Windows start-at-login state for the Vibe Seller tray.

The installer keeps a "Vibe Seller.lnk" in the user's Startup folder
(``{userstartup}`` in vibe-seller.iss) and RE-CREATES it on every
install and upgrade, so the shortcut's existence is not a switch.
Windows' own per-item switch is the StartupApproved flag it keeps for
each Startup-folder entry — the very flag Task Manager and Windows
Settings toggle — keyed by the shortcut's file name under::

    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\
      StartupApproved\\StartupFolder

We read and write that flag. The installer never touches it, so an
upgrade keeps the user's choice; disabling keeps the shortcut, so
Settings still lists the app and the user can re-enable it there.

Stdlib-only, and importing on Linux is safe (the winreg / shell-API
touches are lazy; the codec helpers below are pure and unit-tested on
any OS — see tests/unit/test_tray_autostart.py).
"""

from __future__ import annotations

import ctypes
from datetime import UTC, datetime
import logging
import os
from pathlib import Path
import subprocess
import sys

logger = logging.getLogger('vibe-seller-tray')

# The file name the .iss [Icons] entry creates:
# ``#define AppName "Vibe Seller"`` + '.lnk' (pinned by tests).
LNK_NAME = 'Vibe Seller.lnk'
APPROVED_KEY = (
    r'Software\Microsoft\Windows\CurrentVersion\Explorer'
    r'\StartupApproved\StartupFolder'
)

_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)

# Every path rides in through the environment, never spliced into this
# command string, so a dir with a quote or a space cannot break it.
_PS_CREATE_SHORTCUT = '\n'.join([
    '$sc = (New-Object -ComObject WScript.Shell).CreateShortcut($env:VS_LNK)',
    '$sc.TargetPath = $env:VS_TARGET',
    '$sc.Arguments = $env:VS_ARGS',
    '$sc.WorkingDirectory = $env:VS_WORKDIR',
    '$sc.IconLocation = $env:VS_ICON',
    '$sc.Save()',
])


class AutoStartError(RuntimeError):
    """The requested start-at-login change did not take effect."""


def _to_filetime(dt: datetime) -> int:
    """100 ns ticks since 1601-01-01 UTC (a Windows FILETIME)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = dt - _FILETIME_EPOCH
    return (
        delta.days * 86_400 + delta.seconds
    ) * 10_000_000 + delta.microseconds * 10


def encode_approval(enabled: bool, now: datetime | None = None) -> bytes:
    """The 12-byte StartupApproved value for a Startup-folder entry.

    Enabled is ``02 00 00 00`` + 8 zero bytes; disabled is
    ``03 00 00 00`` + the moment of disabling as a little-endian
    FILETIME (that is what Windows itself writes).
    """
    if enabled:
        return b'\x02' + b'\x00' * 11
    if now is None:
        now = datetime.now(UTC)
    return b'\x03\x00\x00\x00' + _to_filetime(now).to_bytes(8, 'little')


def is_approved(value: bytes | None) -> bool:
    """Whether a StartupApproved value means "runs at logon".

    Absent or empty is enabled (the shell default). Otherwise Windows
    reads bit 0 of the first byte: odd = disabled, even = enabled —
    it writes ``06`` too, so never compare to ``02`` exactly.
    """
    if not value:
        return True
    return value[0] % 2 == 0


def _winreg():
    """winreg, imported lazily so this module also loads on Linux."""
    import winreg  # noqa: PLC0415  # Windows-only stdlib module

    return winreg


class _GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', ctypes.c_ulong),
        ('Data2', ctypes.c_ushort),
        ('Data3', ctypes.c_ushort),
        ('Data4', ctypes.c_ubyte * 8),
    ]


# FOLDERID_Startup {B97D20BB-F46A-4C97-BA10-5E3608430854}
_FOLDERID_STARTUP = _GUID(
    0xB97D20BB,
    0xF46A,
    0x4C97,
    (ctypes.c_ubyte * 8)(0xBA, 0x10, 0x5E, 0x36, 0x08, 0x43, 0x08, 0x54),
)


def _sh_get_known_folder(folder_id: _GUID) -> Path | None:
    """SHGetKnownFolderPath — the shell's own answer, redirections
    included. None anywhere off Windows or on any API failure."""
    if sys.platform != 'win32':
        return None
    try:
        buf = ctypes.c_void_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(buf)
        )
        if hr != 0 or not buf.value:
            return None
        try:
            return Path(ctypes.wstring_at(buf))
        finally:
            ctypes.windll.ole32.CoTaskMemFree(buf)
    except (OSError, ValueError):
        return None


def _registry_startup_dir() -> Path | None:
    """The ``Startup`` value of HKCU User Shell Folders, expanded —
    where the folder is redirected, that is what the shell uses."""
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Explorer'
            r'\User Shell Folders',
        ) as key:
            raw, _ = winreg.QueryValueEx(key, 'Startup')
    except OSError:
        return None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        path = Path(winreg.ExpandEnvironmentStrings(raw))
    except OSError:
        return None
    return path if path.is_absolute() else None


def startup_dir() -> Path:
    """The user's Startup folder.

    The shell resolves this itself (the folder CAN be redirected), so
    ask the shell first, then the User Shell Folders entry; the
    %APPDATA% default is only the last fallback.
    """
    folder = _sh_get_known_folder(_FOLDERID_STARTUP)
    if folder is not None:
        return folder
    folder = _registry_startup_dir()
    if folder is not None:
        return folder
    appdata = os.environ.get('APPDATA')
    base = Path(appdata) if appdata else Path.home() / 'AppData' / 'Roaming'
    return (
        base / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Startup'
    )


def _default_install_dir() -> Path:
    """The install root. The installer sets VIBE_SELLER_HOME; the
    shipped layout puts this file directly in the install dir."""
    return Path(os.environ.get('VIBE_SELLER_HOME') or Path(__file__).parent)


class AutoStart:
    """The start-at-login switch for one Startup-folder shortcut.

    Everything environment-specific is a constructor parameter
    (defaults: the real ones), so tests can point a whole instance at
    a scratch registry key and a temp dir.
    """

    def __init__(
        self,
        startup_dir: Path | str | None = None,
        approved_key: str = APPROVED_KEY,
        lnk_name: str = LNK_NAME,
        install_dir: Path | str | None = None,
    ) -> None:
        self._startup_dir = Path(startup_dir) if startup_dir else None
        self.approved_key = approved_key
        self.lnk_name = lnk_name
        self.install_dir = (
            Path(install_dir) if install_dir else _default_install_dir()
        )

    @property
    def lnk_path(self) -> Path:
        if self._startup_dir is None:
            self._startup_dir = startup_dir()
        return self._startup_dir / self.lnk_name

    def _shortcut_exists(self) -> bool:
        return self.lnk_path.is_file()

    def _read_flag(self) -> bytes | None:
        winreg = _winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, self.approved_key
            ) as key:
                value, _ = winreg.QueryValueEx(key, self.lnk_name)
        except OSError:
            # No key, or no value under it: the shell default, enabled.
            return None
        return bytes(value) or None

    def _write_flag(self, data: bytes) -> None:
        winreg = _winreg()
        # CreateKey creates the whole path if missing — the real key
        # always exists on Windows, a test's scratch key doesn't.
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, self.approved_key
        ) as key:
            winreg.SetValueEx(key, self.lnk_name, 0, winreg.REG_BINARY, data)

    def _create_shortcut(self) -> None:
        """Re-create the Startup shortcut exactly as the .iss [Icons]
        entry defines it (pythonw.exe + tray.py, no --open, icon),
        derived from install_dir — never from sys.executable."""
        env = dict(os.environ)
        tray_py = str(self.install_dir / 'tray.py')
        env.update({
            'VS_LNK': str(self.lnk_path),
            'VS_TARGET': str(
                self.install_dir / '.venv' / 'Scripts' / 'pythonw.exe'
            ),
            # Exactly the .iss Parameters: the quoted tray path, no
            # --open (a login start must not pop a browser window).
            'VS_ARGS': f'"{tray_py}"',
            'VS_WORKDIR': str(self.install_dir),
            'VS_ICON': str(self.install_dir / 'vibe-seller.ico'),
        })
        creationflags = 0
        if sys.platform == 'win32':
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        try:
            proc = subprocess.run(
                [
                    'powershell.exe',
                    '-NoProfile',
                    '-ExecutionPolicy',
                    'Bypass',
                    '-Command',
                    _PS_CREATE_SHORTCUT,
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=creationflags,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AutoStartError(
                f'creating the Startup shortcut failed: {exc}'
            ) from exc
        if proc.returncode != 0 or not self._shortcut_exists():
            detail = (proc.stderr or proc.stdout).strip()[:300]
            raise AutoStartError(
                'creating the Startup shortcut failed: '
                + (detail or f'powershell exited {proc.returncode}')
            )

    def is_enabled(self) -> bool:
        """Whether Windows will run the shortcut at next logon: it
        exists AND the flag reads approved. May raise (registry or
        shell errors) — callers decide how to degrade."""
        return self._shortcut_exists() and is_approved(self._read_flag())

    def set_enabled(self, on: bool) -> bool:
        """Apply the switch and return the state actually in effect.

        Off writes only the disabled flag — the shortcut stays, so
        Windows Settings still lists the app. On re-creates a missing
        shortcut first, then writes the enabled flag. Raises
        AutoStartError when the re-read disagrees with the request
        (e.g. a refused registry write).
        """
        if on:
            if not self._shortcut_exists():
                self._create_shortcut()
            self._write_flag(encode_approval(True))
        else:
            self._write_flag(encode_approval(False))
        state = self.is_enabled()
        if state != on:
            raise AutoStartError(
                'requested start-at-login '
                + ('on' if on else 'off')
                + ' but the state reads '
                + ('on' if state else 'off')
            )
        return state
