"""Locate and launch an installed macOS app (an anti-detect client).

The macOS sibling of :mod:`app.browser.win_app_locator`: one reusable,
bounded discovery so a vendor's Mac client is found wherever it was
installed. Ordered hints; first bundle that validates wins:

1. ``saved`` — a caller-saved path (the vendor's ``client_path``
   setting): the ``.app`` bundle itself, the executable inside it, or a
   directory directly containing one of the spec's bundle names.
2. ``process`` — a running process whose executable sits inside a
   bundle named in the spec (the client is already up).
3. ``spotlight`` — ``mdfind`` by ``CFBundleIdentifier`` (finds the app
   anywhere Spotlight indexes).
4. ``applications`` — the conventional ``/Applications`` and
   ``~/Applications`` folders, by bundle name.

Validation: the bundle has a readable ``Contents/Info.plist`` whose
``CFBundleExecutable`` exists at ``Contents/MacOS/<exe>``. Nothing is
executed during discovery.

``launch`` starts the app through ``open`` (detached) and never logs or
otherwise surfaces the arguments it was given — a caller may pass
secrets on that command line, so they stay out of logs and errors.

All functions are blocking; call from async code via ``asyncio.to_thread``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import os
import plistlib
import subprocess

import psutil

__all__ = [
    'MacAppProcess',
    'MacAppSpec',
    'LocatedMacApp',
    'MacLocatorRunners',
    'app_processes',
    'default_runners',
    'launch',
    'locate',
    'not_found_message',
]

logger = logging.getLogger(__name__)


# ── specs and results -------------------------------------------------


@dataclass(frozen=True)
class MacAppSpec:
    """Everything the locator needs to know about one macOS app.

    Attributes:
        key: Registry key, for messages and logs (the vendor key).
        display_name: Human name for messages (e.g. 'Example').
        bundle_ids: ``CFBundleIdentifier`` values, for the Spotlight
            query (a vendor may re-brand between versions).
        app_names: ``<Name>.app`` bundle names, matched exactly against
            the bundle directory's basename.
    """

    key: str
    display_name: str
    bundle_ids: tuple[str, ...] = ()
    app_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class LocatedMacApp:
    """A validated ``.app`` bundle plus where discovery found it."""

    app_path: str  # the .app bundle directory
    exe_path: str  # Contents/MacOS/<CFBundleExecutable>
    bundle_id: str
    hint: str = ''  # saved | process | spotlight | applications
    detail: str = ''  # what produced the hit (logs/diagnostics)


@dataclass(frozen=True)
class MacAppProcess:
    """One process of the app (main or helper), as the caller sees it."""

    pid: int
    exe: str
    args: tuple[str, ...]


# ── runners (injectable in tests; defaults hit the real system) -------


@dataclass(frozen=True)
class MacLocatorRunners:
    """The system probes the locator uses; every one is injectable.

    Defaults are blocking and safe on any OS: off macOS they find
    nothing (no ``mdfind``, no ``open``), and the process list simply
    has no matching executable.
    """

    mdfind: Callable[[str], list[str]]
    processes: Callable[[], list[MacAppProcess]]
    open_app: Callable[[str, tuple[str, ...]], tuple[int, str]]
    app_dirs: Callable[[], list[str]]


def _default_mdfind(bundle_id: str) -> list[str]:
    """Spotlight query for the bundle id; app-bundle paths it returns."""
    try:
        proc = subprocess.run(
            [
                'mdfind',
                f'kMDItemCFBundleIdentifier == "{bundle_id}"',
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug('mdfind failed: %s', e)
        return []
    return [
        ln.strip()
        for ln in (proc.stdout or '').splitlines()
        if ln.strip().endswith('.app')
    ]


def _default_processes() -> list[MacAppProcess]:
    """Every running process with a readable executable (psutil)."""
    out: list[MacAppProcess] = []
    for p in psutil.process_iter(['pid', 'exe', 'cmdline']):
        try:
            exe = p.info['exe']
            if not exe:
                continue
            cmdline = p.info['cmdline'] or []
            out.append(
                MacAppProcess(
                    pid=p.info['pid'],
                    exe=exe,
                    args=tuple(str(a) for a in cmdline),
                )
            )
        except (psutil.Error, OSError):
            continue
    return out


def _default_open_app(app_path: str, args: tuple[str, ...]) -> tuple[int, str]:
    """``open <app> [--args …]``, detached; returns (rc, stderr).

    The error text never carries the command line: ``args`` may hold
    secrets, and a timeout's exception text embeds the full command, so
    failures are reduced to a fixed message / ``strerror``.
    """
    cmd = ['open', app_path]
    if args:
        cmd += ['--args', *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
    except subprocess.TimeoutExpired:
        return 1, 'open timed out after 30s'
    except OSError as e:
        return 1, e.strerror or 'open failed'
    return proc.returncode, (proc.stderr or '')


def _default_app_dirs() -> list[str]:
    """The folders LaunchServices itself always scans."""
    return ['/Applications', os.path.expanduser('~/Applications')]


def default_runners() -> MacLocatorRunners:
    """The real-system probes (names read at call time; tests patch them)."""
    return MacLocatorRunners(
        mdfind=_default_mdfind,
        processes=_default_processes,
        open_app=_default_open_app,
        app_dirs=_default_app_dirs,
    )


# ── bundle validation --------------------------------------------------


def _bundle_root(path: str) -> str | None:
    """The outermost ``.app`` bundle directory containing ``path``.

    ``/x/Example.app`` itself, or the bundle of an executable nested
    anywhere inside one (``…/Example.app/Contents/MacOS/Example`` →
    ``…/Example.app``). ``None`` when the path is not in a bundle.
    """
    p = path.strip().rstrip('/')
    if p.endswith('.app'):
        return p
    marker = '.app/'
    idx = p.find(marker)
    if idx < 0:
        return None
    return p[: idx + len('.app')]


def _from_bundle(app_path: str, hint: str, detail: str) -> LocatedMacApp | None:
    """Validate a ``.app`` dir via its Info.plist and executable."""
    if not app_path or not os.path.isdir(app_path):
        return None
    try:
        with open(os.path.join(app_path, 'Contents', 'Info.plist'), 'rb') as f:
            plist = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException):
        return None
    exe_name = plist.get('CFBundleExecutable')
    if not isinstance(exe_name, str) or not exe_name:
        return None
    exe_path = os.path.join(app_path, 'Contents', 'MacOS', exe_name)
    if not os.path.isfile(exe_path):
        return None
    bundle_id = plist.get('CFBundleIdentifier')
    return LocatedMacApp(
        app_path=app_path.rstrip('/'),
        exe_path=exe_path,
        bundle_id=bundle_id if isinstance(bundle_id, str) else '',
        hint=hint,
        detail=detail,
    )


# ── the locator --------------------------------------------------------


def locate(
    spec: MacAppSpec,
    saved_path: str | None = None,
    *,
    runners: MacLocatorRunners | None = None,
) -> LocatedMacApp | None:
    """Find the app's ``.app`` bundle; ``None`` when no hint pans out.

    Blocking (subprocess/process scan/filesystem); call via
    ``asyncio.to_thread``. Every hint logs at INFO what it tried and
    what it returned, so a real macOS run shows exactly which hint
    found the app (or why none did).
    """
    runners = runners or default_runners()

    def found(hit: LocatedMacApp) -> LocatedMacApp:
        logger.info(
            '%s: located via %s (%s): %s',
            spec.key,
            hit.hint,
            hit.detail,
            hit.app_path,
        )
        return hit

    # 1. The saved path — operator intent: the .app bundle, the exe
    #    inside it, or a dir directly containing a known bundle name.
    saved = (saved_path or '').strip().rstrip('/')
    if saved:
        candidates: list[tuple[str, str]] = []
        bundle = _bundle_root(saved)
        if bundle:
            candidates.append((bundle, 'saved bundle path'))
        for name in spec.app_names:
            candidates.append((os.path.join(saved, name), f'saved dir/{name}'))
        for cand, detail in candidates:
            hit = _from_bundle(cand, 'saved', detail)
            if hit:
                return found(hit)
        logger.info(
            '%s: hint=saved %r is not a valid bundle; continuing discovery',
            spec.key,
            saved,
        )

    # 2. A running process whose exe sits inside a named bundle.
    try:
        procs = runners.processes()
    except Exception as e:  # a process scan must never break discovery
        logger.info('%s: hint=process failed: %s', spec.key, e)
        procs = []
    names = {n.lower() for n in spec.app_names}
    for proc in procs:
        bundle = _bundle_root(proc.exe)
        if not bundle or os.path.basename(bundle).lower() not in names:
            continue
        hit = _from_bundle(bundle, 'process', f'pid {proc.pid}')
        if hit:
            return found(hit)
    logger.info(
        '%s: hint=process found no executable inside %s',
        spec.key,
        '/'.join(spec.app_names) or '(no bundle names)',
    )

    # 3. Spotlight, by bundle id — finds the app anywhere on disk.
    for bundle_id in spec.bundle_ids:
        try:
            hits = runners.mdfind(bundle_id)
        except Exception as e:
            logger.info('%s: hint=spotlight failed: %s', spec.key, e)
            continue
        for cand in hits:
            hit = _from_bundle(cand, 'spotlight', f'mdfind {bundle_id}')
            if hit:
                return found(hit)
        logger.info(
            '%s: hint=spotlight (bundle id %s) returned %d hit(s), none valid',
            spec.key,
            bundle_id,
            len(hits),
        )

    # 4. The conventional Applications folders, by bundle name.
    for base in runners.app_dirs():
        for name in spec.app_names:
            hit = _from_bundle(
                os.path.join(base, name),
                'applications',
                base,
            )
            if hit:
                return found(hit)
    logger.info('%s: no hint located the app bundle', spec.key)
    return None


def not_found_message(spec: MacAppSpec) -> str:
    """Actionable message listing everything that was searched."""
    names = ' or '.join(spec.app_names) or spec.display_name
    return (
        f'Could not find {spec.display_name} ({names}). Searched: the '
        'saved client path, the running app, Spotlight, and the '
        '/Applications and ~/Applications folders. Install '
        f'{spec.display_name} to /Applications (or open it once so the '
        'path can be discovered), then retry.'
    )


def launch(
    located: LocatedMacApp,
    args: tuple[str, ...] = (),
    *,
    runners: MacLocatorRunners | None = None,
) -> None:
    """Start the app with ``open`` (detached).

    ``args`` are passed through ``open --args`` and may carry secrets:
    they are never logged and never appear in the raised error — its
    text carries only the app path, the return code and ``open``'s
    stderr (truncated). Raises ``RuntimeError`` when ``open`` fails.
    """
    runners = runners or default_runners()
    rc, stderr = runners.open_app(located.app_path, tuple(args))
    if rc != 0:
        raise RuntimeError(
            f'could not launch {located.app_path} (rc={rc}): '
            f'{stderr.strip()[:300]}'
        )


def app_processes(
    located: LocatedMacApp,
    *,
    runners: MacLocatorRunners | None = None,
) -> list[MacAppProcess]:
    """Every process whose executable is inside the app bundle.

    Main and helper processes alike (helpers carry ``--type=`` in their
    args); the caller filters. Blocking; ``asyncio.to_thread`` it.
    """
    runners = runners or default_runners()
    root = os.path.realpath(located.app_path)
    try:
        procs = runners.processes()
    except Exception as e:
        logger.info('%s: process scan failed: %s', located.bundle_id, e)
        return []
    out = []
    for proc in procs:
        if not proc.exe:
            continue
        try:
            exe = os.path.realpath(proc.exe)
        except OSError:
            continue
        if exe == root or exe.startswith(root + os.sep):
            out.append(proc)
    return out
