"""
Shared utilities for Ziniao browser integration.

Handles launching the Ziniao client and communicating with its HTTP API
for browser automation.
"""

import asyncio
import json
import logging
import os
import platform
import re
import subprocess
import threading

import httpx

from app.browser import win_app_locator as win_locator
from app.browser.proxy_env import browser_launch_env
from app.config import LOCALHOST
from app.platform import IS_LINUX, IS_MAC, IS_WINDOWS

logger = logging.getLogger(__name__)


def is_wsl() -> bool:
    """Detect if running under Windows Subsystem for Linux."""
    if not IS_LINUX:
        return False
    try:
        with open('/proc/version', 'r') as f:
            version = f.read().lower()
            return 'microsoft' in version or 'wsl' in version
    except Exception:
        return False


def get_ziniao_host() -> str:
    """Get the host address to reach Ziniao.

    Under WSL2 NAT mode, 127.0.0.1 is WSL's own loopback and cannot
    reach Windows services.  Use the default gateway IP instead, which
    is the Windows host in WSL2.
    """
    if is_wsl():
        try:
            result = subprocess.run(
                ['ip', 'route', 'show', 'default'],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                logger.warning(
                    'ip route failed (rc=%d), falling back to %s',
                    result.returncode,
                    LOCALHOST,
                )
                return LOCALHOST
            # Parse "default via X.X.X.X ..." with regex
            # to handle varying output formats.
            match = re.search(
                r'via\s+(\d+\.\d+\.\d+\.\d+)',
                result.stdout,
            )
            if match:
                gateway = match.group(1)
                logger.debug(
                    'WSL detected, using Windows host IP: %s',
                    gateway,
                )
                return gateway
            logger.warning(
                'Could not parse gateway from: %s',
                result.stdout.strip(),
            )
        except Exception as e:
            logger.warning('Failed to detect WSL gateway IP: %s', e)
    return LOCALHOST


# Cache the host so we don't shell out on every HTTP call.
_ziniao_host_cache: dict[str, str | None] = {'value': None}
_ZINIAO_HOST_LOCK = threading.Lock()


def ziniao_host() -> str:
    if _ziniao_host_cache['value'] is None:
        with _ZINIAO_HOST_LOCK:
            if _ziniao_host_cache['value'] is None:
                _ziniao_host_cache['value'] = get_ziniao_host()
    return _ziniao_host_cache['value']


async def send_http(
    port: int, data: dict, timeout: float = 120, host: str | None = None
) -> dict | None:
    """Send command to ziniao browser via HTTP.

    If host is not specified, uses the cached ziniao_host().
    """
    target_host = (
        host if host is not None else await asyncio.to_thread(ziniao_host)
    )
    try:
        # Disable proxy — Ziniao is always local/LAN.
        async with httpx.AsyncClient(
            timeout=timeout, trust_env=False
        ) as client:
            resp = await client.post(
                f'http://{target_host}:{port}', content=json.dumps(data)
            )
            return resp.json()
    except Exception as e:
        logger.debug('Ziniao HTTP error to %s:%d: %s', target_host, port, e)
        return None


async def try_connect_ziniao(
    port: int, data: dict, timeout: float
) -> tuple[dict | None, str]:
    """Try to connect to Ziniao, returning (result, host_used).

    On WSL, tries 127.0.0.1 first (works in mirrored networking mode),
    then falls back to the Windows gateway IP (works in NAT mode).
    On other platforms, uses the standard host resolution.
    """
    if not is_wsl():
        # Non-WSL: use standard host resolution
        host = await asyncio.to_thread(ziniao_host)
        result = await send_http(port, data, timeout=timeout, host=host)
        return result, host

    # WSL: try localhost first (mirrored mode)
    result = await send_http(port, data, timeout=timeout, host=LOCALHOST)
    if result is not None:
        logger.debug('Connected to Ziniao via %s (mirrored mode)', LOCALHOST)
        return result, LOCALHOST

    # Fall back to gateway IP (NAT mode)
    gateway = await asyncio.to_thread(ziniao_host)
    logger.debug(
        '%s failed, trying gateway IP %s (NAT mode)', LOCALHOST, gateway
    )
    result = await send_http(port, data, timeout=timeout, host=gateway)
    return result, gateway


def find_ziniao_exe_windows() -> str | None:
    """Best-effort resolution of the ziniao.exe full path on Windows.

    Native Windows can launch Ziniao directly (unlike WSL, where the
    server is a Linux process that cannot spawn a Windows Electron app
    with custom flags). But the launch needs the real executable path —
    the stored ``client_path`` defaults to the bare name ``ziniao``,
    which only resolves on macOS via ``open -a``.

    Discovery is the shared Windows app locator
    (:mod:`app.browser.win_app_locator`): the exe path remembered from
    the client process observed before it was stopped, registry App
    Paths + Uninstall keys, Desktop/Start-Menu shortcuts, then bounded
    conventional dirs on every fixed drive — all validated (the result
    is a real ``ziniao.exe``, never an uninstaller) and logged per
    hint. Returns an absolute path, or None if Ziniao can't be found.
    """
    if not IS_WINDOWS:
        return None
    located = win_locator.locate(win_locator.ZINIAO_SPEC)
    return located.exe_path if located else None


def _resolve_windows_client_path(client_path: str) -> str:
    """Resolve a launchable ziniao.exe path on Windows.

    A caller-supplied ``.exe`` path is trusted as-is (covers a
    user-configured client_path). Otherwise — notably the default bare
    ``ziniao`` — discover the install via ``find_ziniao_exe_windows``.
    Raises RuntimeError with an actionable message when Ziniao can't be
    located.
    """
    if client_path and client_path.lower().endswith('.exe'):
        return client_path
    found = find_ziniao_exe_windows()
    if found:
        return found
    raise RuntimeError(win_locator.not_found_message(win_locator.ZINIAO_SPEC))


def build_launch_cmd(client_path: str, socket_port: int) -> list[str]:
    """Build platform-specific command to launch Ziniao client.

    Mac: tested and working (client_path="ziniao" launches
         from /Applications).
    Windows: launches ziniao.exe directly. The default bare name
             "ziniao" is resolved to a real install path via
             find_ziniao_exe_windows(); a caller-supplied full path
             ("D:\\path\\to\\ziniao.exe") is used as-is.
    Linux: client_path should be like "/opt/ziniao/ziniaobrowser".
    WSL: cannot launch Electron apps with custom args due to
         Node.js V8 flag rejection. Users must run the launcher
         batch file on Windows first.
    """
    if IS_WINDOWS:
        exe = _resolve_windows_client_path(client_path)
        return [
            exe,
            '--run_type=web_driver',
            '--ipc_type=http',
            f'--port={socket_port}',
        ]
    elif IS_MAC:
        return [
            'open',
            '-a',
            client_path,
            '--args',
            '--run_type=web_driver',
            '--ipc_type=http',
            f'--port={socket_port}',
        ]
    elif IS_LINUX:
        if is_wsl():
            # WSL cannot pass custom --flags to Electron apps;
            # Node.js V8 rejects unknown flags before app code
            # runs. The user must launch via the .bat script on
            # Windows.
            raise RuntimeError(
                'WSL cannot launch Ziniao automatically. '
                'Please download and run the launcher script '
                'on Windows first:\n'
                '  1. Download: '
                '/api/ziniao/launcher\n'
                '  2. Double-click ziniao_webdriver.bat '
                'on Windows\n'
                '  3. Wait for "ready" message, then retry'
            )
        else:
            # Native Linux
            return [
                client_path,
                '--no-sandbox',
                '--run_type=web_driver',
                '--ipc_type=http',
                f'--port={socket_port}',
            ]
    else:
        raise RuntimeError(f'Unsupported platform: {platform.system()}')


# Conventional .app locations LaunchServices itself always scans; the
# fallback when Spotlight (mdfind) misses an app — its index excludes
# folders (privacy), is disabled, or has not finished its first scan.
_MAC_APP_DIRS = ('/Applications', os.path.expanduser('~/Applications'))


def is_ziniao_installed_mac() -> bool:
    """Check if Ziniao is installed on Mac.

    Uses Spotlight (mdfind) first for detection anywhere on disk, then
    falls back to the conventional Applications folders, which
    LaunchServices itself scans — Spotlight can miss an app that sits
    in an excluded folder or before its first index scan completes.
    """
    if not IS_MAC:
        return False
    try:
        result = subprocess.run(
            [
                'mdfind',
                'kMDItemFSName == "ziniao.app"',
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.stdout.strip():
            return True
    except Exception as e:
        logger.debug('Failed to check Ziniao install: %s', e)
    for base in _MAC_APP_DIRS:
        if os.path.isdir(os.path.join(base, 'ziniao.app')):
            return True
    return False


def get_platform() -> str:
    """Return platform identifier for frontend display."""
    if IS_MAC:
        return 'mac'
    if IS_WINDOWS:
        return 'windows'
    if is_wsl():
        return 'wsl'
    return 'linux'


def launch_ziniao(client_path: str, socket_port: int) -> None:
    """Start the Ziniao client in WebDriver mode (blocking: ``to_thread``).

    Raises when the launch itself is refused, so a missing install fails
    now instead of after a 120 s readiness wait. On macOS ``open -a``
    exits non-zero when it cannot find the app; elsewhere ``Popen``
    raises when the executable is missing.
    """
    cmd = build_launch_cmd(client_path, socket_port)
    if IS_MAC:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env=browser_launch_env(),
        )
        if result.returncode != 0:
            raise RuntimeError(
                (result.stderr or result.stdout).strip()
                or f'`open -a {client_path}` failed (rc={result.returncode})'
            )
        return
    subprocess.Popen(cmd, env=browser_launch_env())


async def ensure_ziniao_running(
    socket_port: int, client_path: str, user_info: dict, **kwargs
) -> None:
    """Return once Ziniao answers in WebDriver mode, (re)launching it.

    The public entry point; the implementation (kill-all, 120 s startup
    deadline, single-flight) is ``app.browser.ziniao_startup``, imported
    here lazily because it builds on this module.
    """
    from app.browser import ziniao_startup  # noqa: PLC0415

    await ziniao_startup.ensure_ziniao_running(
        socket_port, client_path, user_info, **kwargs
    )
