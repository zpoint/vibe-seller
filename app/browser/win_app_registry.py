"""The Windows-registry lookup half of the install locator.

:mod:`app.browser.win_app_locator` runs the ordered hint discovery for
an installed Windows app; THIS module is the piece of it that talks to
the Windows registry — App Paths and the Uninstall table, through
native ``winreg`` where available and a UTF-8-pinned PowerShell
fallback under WSL (a container key queried without its child wildcard
returns the container's own empty values, not its children — the
fallback enumerates the children explicitly).

Split out of the locator so each module stays under the 800-line
review limit while growing. Everything here that the locator (or its
importers) need is re-exported FROM :mod:`app.browser.win_app_locator`
— import it from there; this module is the implementation home.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import subprocess

try:  # Native Windows only; WSL/macOS/Linux have no winreg.
    import winreg  # noqa: PLC0415
except ImportError:
    winreg = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_PS = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command']
_UTF8 = '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;'


def run_powershell(script: str, timeout: float = 30.0) -> str:
    """Run PowerShell, return UTF-8 stdout ('' on any failure).

    Output is UTF-8 on both sides, so non-ASCII paths survive any
    console codepage. Failures are never silent: they log at WARNING,
    since the per-hint INFO logs only count results.
    """
    try:
        proc = subprocess.run(
            [*_PS, _UTF8 + script],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning('powershell call failed: %s', e)
        return ''
    stderr = (proc.stderr or '').strip()
    if proc.returncode != 0 or stderr:
        logger.warning(
            'powershell exited rc=%s: %s', proc.returncode, stderr[:300]
        )
    return proc.stdout or ''


@dataclass(frozen=True)
class UninstallEntry:
    """One registry Uninstall record (winreg or PowerShell fallback)."""

    source: str  # e.g. 'HKCU\\...\\Uninstall\\<subkey>'
    display_name: str = ''
    install_location: str = ''
    display_icon: str = ''
    uninstall_string: str = ''


# ---- registry: native winreg, PowerShell fallback under WSL ----------

_APP_PATHS_KEY = r'Software\Microsoft\Windows\CurrentVersion\App Paths'
_UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'


def _reg_roots():
    """(hive, WOW64 view, source label) — the 32-bit view of HKLM is
    what the WOW6432Node path names; HKCU is not redirected, read it
    once."""
    if winreg is None:
        return ()
    return (
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY, 'HKLM'),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY, 'HKLM(32)'),
        (winreg.HKEY_CURRENT_USER, 0, 'HKCU'),
    )


def _winreg_children(subkey: str, value_names: tuple[str, ...]):
    """Yield ``(label, subkey name, {value: str})`` for each child key."""
    for hive, view, label in _reg_roots():
        try:
            root = winreg.OpenKey(hive, subkey, access=winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    child = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                vals = {}
                try:
                    with winreg.OpenKey(
                        root, child, access=winreg.KEY_READ
                    ) as k:
                        for vname in ('', *value_names):
                            try:
                                raw, _ = winreg.QueryValueEx(k, vname)
                            except OSError:
                                raw = ''
                            vals[vname] = raw if isinstance(raw, str) else ''
                except OSError:
                    continue
                yield label, child, vals


def _ps_app_paths() -> list[tuple[str, str]]:
    script = (
        "$keys='HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
        "\\App Paths',"
        "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows"
        "\\CurrentVersion\\App Paths',"
        "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
        "\\App Paths';"
        'Get-ChildItem $keys -ErrorAction SilentlyContinue | '
        'ForEach-Object { '
        '"$($_.PSChildName)`t$($_.GetValue(\'\'))" }'
    )
    out = []
    for line in run_powershell(script).splitlines():
        if '\t' in line:
            name, _, default = line.partition('\t')
            out.append((name.strip(), default.strip()))
    return out


def _default_app_paths() -> list[tuple[str, str]]:
    """``(subkey name, default value)`` for every App Paths child."""
    if winreg is None:  # WSL: ask the Windows side over interop.
        return _ps_app_paths()
    return [
        (child, vals.get('', ''))
        for _label, child, vals in _winreg_children(
            _APP_PATHS_KEY, value_names=()
        )
    ]


def _ps_uninstall_entries() -> list[UninstallEntry]:
    # \* = child keys: Get-ItemProperty on the CONTAINER returns its
    # own (empty) values, so the pre-fix query found no app from WSL.
    script = (
        "$keys='HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
        "\\Uninstall\\*',"
        "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows"
        "\\CurrentVersion\\Uninstall\\*',"
        "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
        "\\Uninstall\\*';"
        'Get-ItemProperty $keys -ErrorAction SilentlyContinue | '
        'ForEach-Object { '
        '"$($_.DisplayName)`t$($_.InstallLocation)`t'
        '$($_.DisplayIcon)`t$($_.UninstallString)`t$($_.PSPath)" }'
    )
    entries = []
    for line in run_powershell(script).splitlines():
        parts = line.split('\t')
        if len(parts) != 5:
            continue
        name, loc, icon, uninst, ps_path = (p.strip() for p in parts)
        source = ps_path.split('::', 1)[-1] if '::' in ps_path else ps_path
        entries.append(
            UninstallEntry(
                source=source,
                display_name=name,
                install_location=loc,
                display_icon=icon,
                uninstall_string=uninst,
            )
        )
    return entries


def _default_uninstall_entries() -> list[UninstallEntry]:
    """Every Uninstall record from HKLM (64+32 views) and HKCU."""
    if winreg is None:
        return _ps_uninstall_entries()
    return [
        UninstallEntry(
            source=f'{label}\\Uninstall\\{child}',
            display_name=vals.get('DisplayName', ''),
            install_location=vals.get('InstallLocation', ''),
            display_icon=vals.get('DisplayIcon', ''),
            uninstall_string=vals.get('UninstallString', ''),
        )
        for label, child, vals in _winreg_children(
            _UNINSTALL_KEY,
            value_names=(
                'DisplayName',
                'InstallLocation',
                'DisplayIcon',
                'UninstallString',
            ),
        )
    ]
