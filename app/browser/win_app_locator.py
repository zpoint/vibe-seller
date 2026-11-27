"""Locate an installed Windows app's main exe (an anti-detect client).

One reusable, bounded discovery replacing per-app lookups that missed
real installs (a custom dir like ``D:\\紫鸟\\ziniao``, another drive).
Ordered hints; first hit passing :func:`is_main_exe` wins:

1. ``configured`` — caller-configured path; a full ``.exe`` is trusted
   as-is (operator intent), a dir resolves to the main exe inside it.
2. ``process`` — exe path observed running earlier, fed by the kill
   step via :func:`remember_process_path`: the launch flow kills every
   client *before* needing the path, so a live query is always too
   late (:func:`app.browser.ziniao_processes.kill_all_ziniao`).
3. ``app_paths`` / ``uninstall`` — registry (native: :mod:`winreg`;
   WSL: PowerShell with UTF-8 output). From an Uninstall entry:
   ``InstallLocation`` dir, then ``DisplayIcon`` (``",N"`` stripped),
   then the parent dir of ``UninstallString`` — never executed.
4. ``shortcut`` — Desktop (user + Public) and Start Menu Programs
   (user + common) ``.lnk`` targets, read from the shortcut files
   (:mod:`app.browser.lnk_file`, code page independent), matched by
   target basename or shortcut/target name tokens (Chinese included).
5. ``conventional`` — bounded candidates on every FIXED drive
   (``X:\\Program Files[ (x86)]\\<name>``, ``X:\\<name>``,
   ``%LOCALAPPDATA%\\Programs\\<name>``). No recursive disk scan.

Validation: an existing ``.exe`` that is the app's main exe (or, when
the name isn't fixed, the largest non-rejected ``.exe`` directly in the
dir) — never an uninstaller, a Chromium kernel (``chrome.exe`` under
``resources\\``) or an Electron helper. What little PowerShell is left
(the registry fallback under WSL, the shortcut folders) forces UTF-8 on
both sides, so non-ASCII paths survive any console codepage. Every hint
logs at INFO what it tried and what it returned, so a real Windows run
shows exactly which hint failed and why.

All functions are blocking; call from async code via ``asyncio.to_thread``.
"""

from __future__ import annotations

from collections.abc import Callable
import ctypes
from dataclasses import dataclass
import logging
import ntpath
import os
import re
import subprocess
import threading

from app.browser import lnk_file
from app.browser.vendors import vendor_spec

try:  # Native Windows only; WSL/macOS/Linux have no winreg.
    import winreg  # noqa: PLC0415
except ImportError:
    winreg = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def __getattr__(name: str):
    """Serve a vendor's install descriptor from the vendor registry.

    Vendor descriptors are declared in the vendor's own module and
    carried on its :class:`~app.browser.vendors.BrowserVendorSpec`
    (``win_app``); this module only runs the discovery. The attributes
    stay for call sites that predate the registry (and tests):
    ``<KEY>_SPEC`` resolves to ``vendor_spec(<key>).win_app``. An
    attribute hook — resolving the registry can load plugins.
    """
    if name.endswith('_SPEC') and name.isupper():
        spec = vendor_spec(name[: -len('_SPEC')].lower())
        if spec is not None and spec.win_app is not None:
            return spec.win_app
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


_PS = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command']
_UTF8 = '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;'

# A main exe is never one of these: uninstallers (any ``uninst*`` name,
# checked separately), Chromium/Electron kernels and helpers that sit in
# or under the install dir but are not the app's main process.
_REJECT_BASENAMES = frozenset({
    'chrome.exe',
    'crashpad_handler.exe',
    'elevation_service.exe',
})
# Path segments that mark a bundled kernel tree, not the install root.
_REJECT_DIR_SEGMENTS = ('resources',)

_DRIVE_FIXED = 3  # GetDriveTypeW


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


def win_path_to_wsl(win_path: str) -> str:
    """``C:\\Users\\x`` → ``/mnt/c/Users/x`` (best-effort, drive letters)."""
    p = win_path.strip().strip('"').replace('\\', '/')
    if len(p) >= 2 and p[1] == ':':
        return f'/mnt/{p[0].lower()}{p[2:]}'
    return p


def win_path_to_local(win_path: str) -> str:
    """Resolve a Windows path to where THIS Python process reads files.

    The Windows clients always report Windows paths (``C:\\...``); the
    server reading the file may itself run as native Windows (open
    ``C:\\...`` directly) or under WSL driving the Windows side (open it
    under ``/mnt/<drive>/``). ``normpath`` also collapses the doubled
    separators a kernel sometimes puts in its spawn args.
    """
    raw = win_path.strip().strip('"')
    if os.name == 'nt':  # read at call time: tests patch os.name
        return os.path.normpath(raw)
    return win_path_to_wsl(raw)


# ---- specs and results -------------------------------------------------


@dataclass(frozen=True)
class WinAppSpec:
    """Everything the locator needs to know about one Windows app.

    Attributes:
        key: Cache/remember-store key (the vendor key, e.g. 'ziniao').
        display_name: Human name for messages ('Ziniao', '悦互联').
        exe_names: Exact main-exe basenames, matched case-insensitively.
            Empty when the exe name is a locale string that changes
            between versions — then the main exe is discovered inside
            the install dir instead (largest non-rejected ``.exe``
            directly in it).
        match_tokens: Case-insensitive substrings identifying the app in
            a registry value, a shortcut/target name or a process path
            (Latin + Chinese spellings).
        dir_names: Install dir names probed under every fixed drive's
            conventional roots; may contain nested names like
            ``'紫鸟\\ziniao'``.
    """

    key: str
    display_name: str
    exe_names: tuple[str, ...] = ()
    match_tokens: tuple[str, ...] = ()
    dir_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class LocatedApp:
    """A validated main exe plus where it came from (logs/diagnostics)."""

    exe_path: str  # Windows-style path, as discovered
    hint: str  # configured | process | app_paths | uninstall | shortcut | conventional
    detail: str  # the registry key / shortcut / dir that produced it


@dataclass(frozen=True)
class UninstallEntry:
    """One registry Uninstall record (winreg or PowerShell fallback)."""

    source: str  # e.g. 'HKCU\\...\\Uninstall\\<subkey>'
    display_name: str = ''
    install_location: str = ''
    display_icon: str = ''
    uninstall_string: str = ''


# ---- validation --------------------------------------------------------


def _basename(path: str) -> str:
    return ntpath.basename(path.replace('/', '\\')).lower()


def is_main_exe(spec: WinAppSpec, win_path: str, to_local=None) -> bool:
    """Whether ``win_path`` can be this app's main executable.

    Must be an existing ``.exe``; never ``uninst*``, never a kernel or
    helper (see ``_REJECT_BASENAMES`` / ``_REJECT_DIR_SEGMENTS``), never
    under ``...\\resources\\``; and for apps with fixed exe names the
    basename must match exactly.
    """
    if not win_path:
        return False
    name = _basename(win_path)
    if not name.endswith('.exe'):
        return False
    if name.startswith('uninst'):
        return False
    if name in _REJECT_BASENAMES:
        return False
    lowered = win_path.replace('/', '\\').lower()
    for seg in _REJECT_DIR_SEGMENTS:
        if f'\\{seg}\\' in lowered:
            return False
    if spec.exe_names and name not in (n.lower() for n in spec.exe_names):
        return False
    local = (to_local or win_path_to_local)(win_path)
    return os.path.isfile(local)


def main_exe_in_dir(
    spec: WinAppSpec, win_dir: str, to_local=None
) -> str | None:
    """The app's main exe inside an install dir (bounded depth).

    Fixed exe names: the exe directly in the dir, or one level down
    (``D:\\紫鸟`` containing ``D:\\紫鸟\\ziniao\\ziniao.exe``). No fixed
    name: the largest non-rejected ``.exe`` directly in the dir — the
    install root always holds the Electron binary, kernels live under
    ``resources\\`` and are rejected by name. No recursion beyond that.
    """
    if not win_dir:
        return None
    to_local = to_local or win_path_to_local
    base = win_dir.strip().strip('"').rstrip('\\')
    local_dir = to_local(base)
    if not os.path.isdir(local_dir):
        return None
    if spec.exe_names:
        for name in spec.exe_names:
            cand = f'{base}\\{name}'
            if is_main_exe(spec, cand, to_local):
                return cand
        try:
            entries = sorted(os.listdir(local_dir))
        except OSError:
            return None
        for entry in entries:  # one level down only
            sub = f'{base}\\{entry}'
            if not os.path.isdir(to_local(sub)):
                continue
            for name in spec.exe_names:
                cand = f'{sub}\\{name}'
                if is_main_exe(spec, cand, to_local):
                    return cand
        return None
    best: str | None = None
    best_size = -1
    try:
        entries = sorted(os.listdir(local_dir))
    except OSError:
        return None
    for entry in entries:
        cand = f'{base}\\{entry}'
        if not is_main_exe(spec, cand, to_local):
            continue
        try:
            size = os.path.getsize(to_local(cand))
        except OSError:
            continue
        if size > best_size:
            best, best_size = cand, size
    return best


# ---- runners (injectable in tests; defaults hit the real system) -------


@dataclass(frozen=True)
class LocatorRunners:
    """The system probes the locator uses; every one is injectable.

    Defaults are blocking and safe on any OS: off Windows/WSL they
    return nothing (no PowerShell, winreg or ``/mnt``).
    """

    app_paths: Callable[[], list[tuple[str, str]]]
    uninstall_entries: Callable[[], list[UninstallEntry]]
    shortcut_targets: Callable[[], list[tuple[str, str]]]
    fixed_drives: Callable[[], list[str]]
    to_local: Callable[[str], str] = win_path_to_local


def default_runners() -> LocatorRunners:
    # Names are read from module globals AT CALL TIME so tests (and
    # WSL/native switches) can patch them.
    return LocatorRunners(
        app_paths=_default_app_paths,
        uninstall_entries=_default_uninstall_entries,
        shortcut_targets=_default_shortcut_targets,
        fixed_drives=fixed_drives,
        to_local=win_path_to_local,
    )


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
    script = (
        "$keys='HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
        "\\Uninstall',"
        "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows"
        "\\CurrentVersion\\Uninstall',"
        "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
        "\\Uninstall';"
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


# ---- shortcuts ---------------------------------------------------------

# Where Windows keeps Desktop and Start-Menu shortcuts, from the shell's
# own known-folder API (a redirected Desktop included). Only these folder
# locations come from PowerShell: the links themselves are read from
# their files by :mod:`app.browser.lnk_file`, because ``WScript.Shell``
# cannot handle a path or target outside the system ANSI code page.
_KNOWN_FOLDERS_PS = (
    "[Environment]::GetFolderPath('Desktop'),"
    "[Environment]::GetFolderPath('CommonDesktopDirectory'),"
    "[Environment]::GetFolderPath('Programs'),"
    "[Environment]::GetFolderPath('CommonPrograms') | "
    'Where-Object { $_ } | Select-Object -Unique'
)


def _default_shortcut_targets() -> list[tuple[str, str]]:
    """``(target path, .lnk path)`` for Desktop + Start Menu shortcuts.

    A link without a filesystem target (MSI "advertised" and Store-app
    shortcuts) or an unreadable one is skipped.
    """
    roots = [
        line.strip().strip('"')
        for line in run_powershell(_KNOWN_FOLDERS_PS).splitlines()
        if line.strip()
    ]
    return lnk_file.shortcut_targets(roots, win_path_to_local)


# ---- drives ------------------------------------------------------------


def fixed_drives() -> list[str]:
    """Drive letters (``'C:'``) of FIXED drives; ``/mnt/<l>`` under WSL."""
    if os.name == 'nt':
        return _windows_fixed_drives()
    if os.path.isdir('/mnt'):  # WSL: every Windows drive is mounted.
        out = []
        try:
            entries = os.listdir('/mnt')
        except OSError:
            return []
        for entry in sorted(entries):
            if len(entry) == 1 and entry.isalpha():
                if os.path.isdir(f'/mnt/{entry}'):
                    out.append(f'{entry.upper()}:')
        return out
    return []


def _windows_fixed_drives() -> list[str]:
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        mask = kernel32.GetLogicalDrives()
        out = []
        for i in range(26):
            if not mask & (1 << i):
                continue
            letter = chr(ord('A') + i)
            root = f'{letter}:\\'
            if kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) == _DRIVE_FIXED:
                out.append(f'{letter}:')
        return out
    except Exception as e:  # no ctypes/winapi — shouldn't happen on NT
        logger.debug('drive enumeration failed: %s', e)
        return []


def conventional_dirs(
    spec: WinAppSpec, drives: list[str] | None = None
) -> list[str]:
    """Bounded install-dir candidates, in probe order.

    ``X:\\Program Files\\<name>``, ``(x86)``, ``X:\\<name>`` for every
    fixed drive, then ``%LOCALAPPDATA%\\Programs\\<name>``.
    """
    drives = fixed_drives() if drives is None else drives
    roots = []
    for drive in drives:
        roots.append(f'{drive}\\Program Files')
        roots.append(f'{drive}\\Program Files (x86)')
    for drive in drives:
        roots.append(f'{drive}\\')
    localappdata = os.environ.get('LOCALAPPDATA')
    if localappdata:
        roots.append(ntpath.join(localappdata, 'Programs'))
    return [
        ntpath.join(root, name) for root in roots for name in spec.dir_names
    ]


# ---- remembered process paths (hint 2, fed by the kill step) -----------

_REMEMBERED: dict[str, str] = {}
_REMEMBERED_LOCK = threading.Lock()


def remember_process_path(key: str, exe_path: str) -> None:
    """Record where the app's main exe was seen running.

    ``kill_all_ziniao`` observes it before stopping the process; the
    launch that follows needs the path after it is gone. Validated
    (file exists, name matches) at lookup time, not here.
    """
    if not exe_path:
        return
    with _REMEMBERED_LOCK:
        _REMEMBERED[key] = exe_path.strip().strip('"')


def remembered_process_path(key: str) -> str | None:
    """The path :func:`remember_process_path` stored (tests/diagnostics)."""
    with _REMEMBERED_LOCK:
        return _REMEMBERED.get(key)


# ---- the locator -------------------------------------------------------

_CACHE: dict[tuple[str, str], LocatedApp] = {}
_CACHE_LOCK = threading.Lock()


def clear_locator_cache() -> None:
    """Forget every cached hit and remembered path (tests)."""
    with _CACHE_LOCK:
        _CACHE.clear()
    with _REMEMBERED_LOCK:
        _REMEMBERED.clear()


def _token_match(spec: WinAppSpec, *values: str) -> bool:
    hay = ' \n'.join(v for v in values if v).lower()
    return any(t.lower() in hay for t in spec.match_tokens)


def _from_uninstall_entry(
    spec: WinAppSpec, entry: UninstallEntry, to_local
) -> LocatedApp | None:
    """Derive a validated exe from one matched Uninstall record.

    Order: ``InstallLocation`` (the installer's own "primary folder"),
    ``DisplayIcon`` (the exe with the icon index stripped; its parent
    dir as a second try), then the parent dir of the ``UninstallString``
    exe — parsed, never executed.
    """
    probes: list[tuple[str, str]] = []
    if entry.install_location:
        loc = entry.install_location.strip().strip('"').rstrip('\\')
        probes.append((loc, 'InstallLocation'))
    icon = entry.display_icon.strip()
    if icon:
        icon = icon.split(',')[0].strip().strip('"')
        probes.append((icon, 'DisplayIcon'))
        probes.append((ntpath.dirname(icon), 'DisplayIcon dir'))
    m = re.search(r'"([^"]+\.exe)"|(\S+\.exe)', entry.uninstall_string or '')
    if m:
        uninst_exe = (m.group(1) or m.group(2)).strip()
        probes.append((ntpath.dirname(uninst_exe), 'UninstallString dir'))
    for cand, field in probes:
        if not cand:
            continue
        exe = is_main_exe(spec, cand, to_local) and cand
        if not exe:
            exe = main_exe_in_dir(spec, cand, to_local)
        if exe:
            return LocatedApp(
                exe_path=exe, hint='uninstall', detail=f'{field}: {cand}'
            )
    return None


def locate(
    spec: WinAppSpec,
    *,
    configured_path: str | None = None,
    runners: LocatorRunners | None = None,
) -> LocatedApp | None:
    """Find the app's main exe; ``None`` when no hint pans out.

    Blocking (subprocess/registry/fs); call via ``asyncio.to_thread``.
    Results are cached per (app, configured path) until the file
    disappears. Every hint logs at INFO what it tried and returned.
    """
    runners = runners or default_runners()
    configured = (configured_path or '').strip().strip('"')
    cache_key = (spec.key, configured)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
    if cached and os.path.isfile(runners.to_local(cached.exe_path)):
        return cached

    def found(hint: str, exe: str, detail: str) -> LocatedApp:
        hit = LocatedApp(exe_path=exe, hint=hint, detail=detail)
        logger.info(
            '%s: located via %s (%s): %s',
            spec.key,
            hint,
            detail,
            exe,
        )
        with _CACHE_LOCK:
            _CACHE[cache_key] = hit
        return hit

    # 1. Configured path — a full .exe is operator intent, trusted
    #    as-is; a directory resolves to the main exe inside it.
    if configured:
        if configured.lower().endswith('.exe'):
            return found('configured', configured, 'client_path')
        exe = main_exe_in_dir(spec, configured, runners.to_local)
        if exe:
            return found('configured', exe, f'client_path dir {configured}')
        logger.info(
            '%s: hint=configured %r is neither a valid exe dir nor '
            'resolvable; continuing discovery',
            spec.key,
            configured,
        )

    # 2. A main-exe path observed before the kill step stopped it.
    seen_exe = remembered_process_path(spec.key)
    if seen_exe:
        if is_main_exe(spec, seen_exe, runners.to_local):
            return found('process', seen_exe, 'seen before kill')
        logger.info(
            '%s: hint=process remembered %r no longer valid; continuing',
            spec.key,
            seen_exe,
        )

    # 3a. App Paths — the shell's own exe-name → full-path mapping.
    try:
        app_paths = runners.app_paths()
    except Exception as e:
        logger.info('%s: hint=app_paths failed: %s', spec.key, e)
        app_paths = []
    else:
        logger.info(
            '%s: hint=app_paths scanned %d key(s)', spec.key, len(app_paths)
        )
    for sub_name, default in app_paths:
        cand = default.strip().strip('"')
        if not cand:
            continue
        if not (
            _basename(cand) in (n.lower() for n in spec.exe_names)
            or _token_match(spec, sub_name, cand)
        ):
            continue
        if is_main_exe(spec, cand, runners.to_local):
            return found('app_paths', cand, f'App Paths\\{sub_name}')
    if app_paths:
        logger.info('%s: hint=app_paths found no valid match', spec.key)

    # 3b. Uninstall records.
    try:
        entries = runners.uninstall_entries()
    except Exception as e:
        logger.info('%s: hint=uninstall failed: %s', spec.key, e)
        entries = []
    else:
        logger.info(
            '%s: hint=uninstall scanned %d entry(ies)',
            spec.key,
            len(entries),
        )
    matched = [
        e
        for e in entries
        if _token_match(
            spec, e.display_name, e.install_location, e.display_icon
        )
    ]
    for entry in matched:
        hit = _from_uninstall_entry(spec, entry, runners.to_local)
        if hit:
            return found(
                hit.hint,
                hit.exe_path,
                f'{entry.source}: {hit.detail}',
            )
    if entries:
        logger.info(
            '%s: hint=uninstall matched %d entry(ies), none gave a valid exe',
            spec.key,
            len(matched),
        )

    # 4. Desktop / Start-Menu shortcuts.
    try:
        shortcuts = runners.shortcut_targets()
    except Exception as e:
        logger.info('%s: hint=shortcut failed: %s', spec.key, e)
        shortcuts = []
    else:
        logger.info(
            '%s: hint=shortcut scanned %d shortcut(s)',
            spec.key,
            len(shortcuts),
        )
    for target, lnk in shortcuts:
        strong = _basename(target) in (n.lower() for n in spec.exe_names)
        weak = _token_match(spec, _basename(lnk)) and _token_match(spec, target)
        if not (strong or weak):
            continue
        if is_main_exe(spec, target, runners.to_local):
            return found('shortcut', target, ntpath.basename(lnk))
    if shortcuts:
        logger.info('%s: hint=shortcut found no valid target', spec.key)

    # 5. Conventional dirs on every fixed drive.
    drives = runners.fixed_drives()
    candidates = conventional_dirs(spec, drives)
    logger.info(
        '%s: hint=conventional probing %d dir(s) on drive(s) %s',
        spec.key,
        len(candidates),
        ', '.join(drives) or '(none)',
    )
    for cand in candidates:
        exe = main_exe_in_dir(spec, cand, runners.to_local)
        if exe:
            return found('conventional', exe, cand)

    with _CACHE_LOCK:
        _CACHE.pop(cache_key, None)
    logger.info('%s: no hint located the main exe', spec.key)
    return None


def not_found_message(spec: WinAppSpec) -> str:
    """Actionable message listing everything that was searched.

    Never tells the user to configure an exe path — there is no UI for
    it. Opening the app by hand is actionable and works with hint 2:
    the next start observes the running client before stopping it and
    remembers where it lives.
    """
    drives = ', '.join(fixed_drives()) or 'none found'
    name = spec.exe_names[0] if spec.exe_names else f'{spec.display_name}'
    return (
        f'Could not find {name}. Searched: the running client '
        '(observed before it was stopped), registry App Paths and '
        'Uninstall keys, Desktop and Start-Menu shortcuts, and the '
        f'conventional install folders on fixed drives ({drives}). '
        f'Open {spec.display_name} once by hand from its shortcut and '
        'retry, or reinstall it to the default location.'
    )
