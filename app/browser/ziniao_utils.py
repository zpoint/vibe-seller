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
import uuid

import httpx

from app import telemetry
from app.config import LOCALHOST
from app.platform import IS_LINUX, IS_MAC, IS_WINDOWS
from app.telemetry_events import BrowserFailureReason, TelemetryEvent

logger = logging.getLogger(__name__)


class ZiniaoNormalModeError(RuntimeError):
    """Ziniao is running in normal mode (not WebDriver)."""

    pass


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
    target_host = host if host is not None else ziniao_host()
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
        host = ziniao_host()
        result = await send_http(port, data, timeout=timeout, host=host)
        return result, host

    # WSL: try localhost first (mirrored mode)
    result = await send_http(port, data, timeout=timeout, host=LOCALHOST)
    if result is not None:
        logger.debug('Connected to Ziniao via %s (mirrored mode)', LOCALHOST)
        return result, LOCALHOST

    # Fall back to gateway IP (NAT mode)
    gateway = ziniao_host()
    logger.debug(
        '%s failed, trying gateway IP %s (NAT mode)', LOCALHOST, gateway
    )
    result = await send_http(port, data, timeout=timeout, host=gateway)
    return result, gateway


def build_launch_cmd(client_path: str, socket_port: int) -> list[str]:
    """Build platform-specific command to launch Ziniao client.

    Mac: tested and working (client_path="ziniao" launches
         from /Applications).
    Windows: client_path should be full path like
             "D:\\path\\to\\ziniao.exe".
    Linux: client_path should be like "/opt/ziniao/ziniaobrowser".
    WSL: cannot launch Electron apps with custom args due to
         Node.js V8 flag rejection. Users must run the launcher
         batch file on Windows first.
    """
    if IS_WINDOWS:
        return [
            client_path,
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


def is_ziniao_process_running() -> bool:
    """Check if Ziniao is already running.

    Supports Windows (tasklist), WSL (cmd.exe tasklist), and
    Mac (pgrep with specific binary path to avoid false positives).
    """
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq ziniao.exe'],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return 'ziniao.exe' in result.stdout.lower()
        elif is_wsl():
            cmd_path = '/mnt/c/Windows/System32/cmd.exe'
            if not os.path.isfile(cmd_path):
                logger.debug(
                    'cmd.exe not found at %s; WSL interop may be disabled',
                    cmd_path,
                )
                return False
            result = subprocess.run(
                [
                    cmd_path,
                    '/c',
                    'tasklist /FI "IMAGENAME eq ziniao.exe"',
                ],
                capture_output=True,
                text=True,
                timeout=10,
                cwd='/mnt/c/',
            )
            return 'ziniao.exe' in result.stdout.lower()
        elif IS_MAC:
            # Match the actual Electron binary path to avoid
            # false positives from editors, grep, etc.
            result = subprocess.run(
                [
                    'pgrep',
                    '-f',
                    'ziniao.app/Contents/MacOS',
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        else:
            return False
    except Exception as e:
        logger.debug('Failed to check Ziniao process: %s', e)
        return False


def is_ziniao_installed_mac() -> bool:
    """Check if Ziniao is installed on Mac.

    Uses Spotlight (mdfind) for reliable detection across
    /Applications, ~/Applications, and other locations.
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
        return bool(result.stdout.strip())
    except Exception as e:
        logger.debug('Failed to check Ziniao install: %s', e)
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


async def get_ziniao_status(
    socket_port: int,
    user_info: dict,
) -> dict:
    """Get structured Ziniao status for frontend display.

    Returns dict with a 'status' key.
    Status values:
      - running_webdriver: API responds, ready to use
      - no_permission: API responds with -10003 (WebDriver
        not enabled)
      - api_error: API responds with other error
      - running_normal: process found but API unreachable
      - not_running: not running but installed (Mac)
      - not_installed: not found on system (Mac)

    The server platform is published separately via
    GET /api/system/info — callers should fetch it once at app
    init instead of relying on each error payload to carry it.
    """
    # Probe the HTTP API
    probe = {
        'action': 'getBrowserList',
        'requestId': str(uuid.uuid4()),
        **user_info,
    }
    result, _ = await try_connect_ziniao(socket_port, probe, timeout=5)
    if result is not None:
        status_code = str(result.get('statusCode', ''))
        if status_code == '0':
            return {'status': 'running_webdriver'}
        if status_code == '-10003':
            return {'status': 'no_permission'}
        return {'status': 'api_error'}

    # API unreachable — check if process is running
    if is_ziniao_process_running():
        return {'status': 'running_normal'}

    # Not running — check if installed (Mac only)
    if IS_MAC:
        if is_ziniao_installed_mac():
            return {'status': 'not_running'}
        return {'status': 'not_installed'}

    return {'status': 'not_running'}


def force_kill_ziniao() -> None:
    """Kill all Ziniao processes (Mac and WSL).

    Mac: SIGKILL via pkill (Ziniao may auto-restart after
    SIGTERM, so -9 is required).
    WSL: taskkill.exe /F via Windows interop.

    Raises RuntimeError on unsupported platforms or if
    WSL interop is disabled.
    """
    try:
        if IS_MAC:
            subprocess.run(
                ['pkill', '-9', '-f', 'ziniao.app/Contents/MacOS'],
                capture_output=True,
                timeout=10,
            )
        elif is_wsl():
            taskkill = '/mnt/c/Windows/System32/taskkill.exe'
            if not os.path.isfile(taskkill):
                raise RuntimeError(
                    f'taskkill.exe not found at {taskkill}; '
                    'WSL interop may be disabled'
                )
            subprocess.run(
                [taskkill, '/F', '/IM', 'ziniao.exe'],
                capture_output=True,
                timeout=10,
                cwd='/mnt/c/',
            )
        else:
            raise RuntimeError('Force kill is only supported on Mac and WSL')
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f'Failed to kill Ziniao: {e}') from e


async def kill_and_relaunch_ziniao(
    socket_port: int,
    client_path: str,
    user_info: dict,
) -> bool:
    """Kill Ziniao and relaunch in WebDriver mode.

    Mac: kills and relaunches automatically, polls for API
    readiness.
    WSL: kills only (cannot auto-relaunch due to Electron
    V8 flag rejection via WSL interop). Caller handles the
    post-kill state.

    Safe to call when Ziniao is in normal mode (no active
    CDP sessions).

    Returns True on success, raises RuntimeError on failure.
    """
    if IS_MAC:
        logger.info('Killing Ziniao for WebDriver relaunch...')
    else:
        logger.info('Killing Ziniao (manual relaunch required)...')
    force_kill_ziniao()

    # Poll for process termination (up to 10 seconds)
    for i in range(20):
        await asyncio.sleep(0.5)
        if not is_ziniao_process_running():
            logger.info('Ziniao terminated after %.1fs', (i + 1) * 0.5)
            break
    else:
        hint = 'Activity Monitor' if IS_MAC else 'Task Manager'
        raise RuntimeError(
            'Failed to terminate Ziniao after 10 seconds. '
            f'Please close it manually via {hint}.'
        )

    # WSL: kill-only (cannot auto-relaunch)
    if not IS_MAC:
        return True

    # Relaunch in WebDriver mode (Mac only)
    logger.info(
        'Relaunching Ziniao in WebDriver mode (client_path=%s, port=%d)',
        client_path,
        socket_port,
    )
    try:
        cmd = build_launch_cmd(client_path, socket_port)
        subprocess.Popen(cmd)
    except Exception as e:
        raise RuntimeError(f'Failed to relaunch Ziniao: {e}')

    # Poll for API readiness (up to 60 seconds).
    # Accept any HTTP response (even -10003 no_permission)
    # as proof that Ziniao is in WebDriver mode.
    probe = {
        'action': 'getBrowserList',
        'requestId': str(uuid.uuid4()),
        **user_info,
    }
    for i in range(30):
        await asyncio.sleep(2)
        result, _ = await try_connect_ziniao(socket_port, probe, timeout=10)
        if result is not None:
            logger.info(
                'Ziniao restarted successfully after %ds',
                (i + 1) * 2,
            )
            return True
        logger.debug('Ziniao not ready yet (%ds)...', (i + 1) * 2)

    raise RuntimeError('Ziniao relaunch timed out after 60 seconds')


async def ensure_ziniao_running(
    socket_port: int,
    client_path: str,
    user_info: dict,
) -> bool:
    """Check if Ziniao is running, launch it if not.

    On WSL, cannot auto-launch due to Electron/Node.js V8 flag
    rejection.  Instead, checks if Ziniao is already reachable
    (trying both localhost and gateway IP) and guides the user
    to the launcher script if not.

    Returns True if Ziniao is reachable, raises RuntimeError
    on failure.
    """
    runtime_os = telemetry.runtime_os()

    def _fire_failed(category: str, attempts: int) -> None:
        try:
            telemetry.send(
                TelemetryEvent.BROWSER_SESSION_FAILED,
                {
                    'backend': 'ziniao',
                    'os': runtime_os,
                    'attempts_total': attempts,
                    'error_category': category,
                },
            )
        except Exception:
            pass

    # Try a quick API call to check if already running.
    # Short timeout on WSL (can't auto-launch, fail fast);
    # longer on other platforms (may need to auto-launch).
    probe_timeout = 3 if is_wsl() else 10
    probe = {
        'action': 'getBrowserList',
        'requestId': str(uuid.uuid4()),
        **user_info,
    }
    try:
        telemetry.send(
            TelemetryEvent.BROWSER_SESSION_ATTEMPTED,
            {'backend': 'ziniao', 'os': runtime_os, 'attempt_number': 1},
        )
    except Exception:
        pass
    result, host_used = await try_connect_ziniao(
        socket_port, probe, timeout=probe_timeout
    )
    if result is not None:
        try:
            telemetry.send(
                TelemetryEvent.BROWSER_SESSION_STARTED,
                {
                    'backend': 'ziniao',
                    'os': runtime_os,
                    'attempts_to_success': 1,
                    'auto_launched': False,
                },
            )
        except Exception:
            pass
        # Validate login status — Ziniao returns a response even
        # when credentials are wrong (e.g. -10003 login error)
        # or the response is malformed (missing statusCode).
        status_code = str(result.get('statusCode', ''))
        if status_code == '0':
            logger.info(
                'Ziniao already running on port %d (via %s)',
                socket_port,
                host_used,
            )
        else:
            err_msg = result.get('err', 'unknown error')
            logger.warning(
                'Ziniao probe returned error (port %d): statusCode=%s, err=%s',
                socket_port,
                status_code,
                err_msg,
            )
        return True

    # API unreachable — WSL cannot auto-launch, guide user
    if is_wsl():
        if is_ziniao_process_running():
            _fire_failed(BrowserFailureReason.WSL_WRONG_PORT, 1)
            raise RuntimeError(
                'Ziniao is running on Windows but not '
                f'listening on port {socket_port}. '
                'Please close Ziniao and re-launch it '
                'using the ziniao_webdriver.bat script.\n'
                'Download: /api/ziniao/launcher'
            )
        _fire_failed(BrowserFailureReason.WSL_NOT_RUNNING, 1)
        raise RuntimeError(
            'Ziniao is not running. WSL cannot launch '
            'Ziniao automatically.\n'
            'Please download and run the launcher script '
            'on Windows:\n'
            '  1. Download: /api/ziniao/launcher\n'
            '  2. Double-click ziniao_webdriver.bat\n'
            '  3. Wait for "ready" message, then retry'
        )

    # Check if Ziniao process exists but API unreachable
    if is_ziniao_process_running():
        if IS_MAC:
            _fire_failed(BrowserFailureReason.NORMAL_MODE, 1)
            raise ZiniaoNormalModeError(
                'Ziniao is running but not in WebDriver '
                'mode. Please close Ziniao and try again.'
            )
        _fire_failed(BrowserFailureReason.WRONG_PORT, 1)
        raise RuntimeError(
            'Ziniao is already running but not listening '
            f'on port {socket_port}. Please close Ziniao '
            'and try again so it can be auto-launched '
            'with the correct port.'
        )

    # Not running — launch it (Mac / Windows / native Linux)
    logger.info(
        'Ziniao not running, launching (client_path=%s, port=%d)...',
        client_path,
        socket_port,
    )
    try:
        cmd = build_launch_cmd(client_path, socket_port)
        subprocess.Popen(cmd)
    except Exception as e:
        _fire_failed(BrowserFailureReason.LAUNCH_FAILED, 1)
        raise RuntimeError(f'Failed to launch Ziniao client: {e}')

    # Poll for readiness (up to 60 seconds).
    # Accept any HTTP response (even -10003 no_permission)
    # as proof that Ziniao is in WebDriver mode. The caller
    # handles permission errors separately.
    for i in range(30):
        try:
            telemetry.send(
                TelemetryEvent.BROWSER_SESSION_ATTEMPTED,
                {
                    'backend': 'ziniao',
                    'os': runtime_os,
                    'attempt_number': i + 2,  # 1 was the initial probe
                },
            )
        except Exception:
            pass
        await asyncio.sleep(2)
        result, _ = await try_connect_ziniao(socket_port, probe, timeout=10)
        if result is not None:
            logger.info(
                'Ziniao started successfully after %ds',
                (i + 1) * 2,
            )
            try:
                telemetry.send(
                    TelemetryEvent.BROWSER_SESSION_STARTED,
                    {
                        'backend': 'ziniao',
                        'os': runtime_os,
                        'attempts_to_success': i + 2,
                        'auto_launched': True,
                    },
                )
            except Exception:
                pass
            return True
        logger.debug('Ziniao not ready yet (%ds)...', (i + 1) * 2)

    _fire_failed(BrowserFailureReason.STARTUP_TIMEOUT, 31)
    raise RuntimeError('Ziniao startup timed out after 60 seconds')
