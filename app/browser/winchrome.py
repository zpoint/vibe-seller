"""
Windows-native Chrome browser backend.

Launches Google Chrome as a native Windows process (via Task Scheduler so
it runs in the interactive desktop session and is visible to the user) and
connects to it via CDP. Designed for WSL2 deployments where Playwright's
headless-shell cannot show a headed window.

Architecture
------------
- Chrome runs on Windows (visible on screen, native performance, no WSLg).
- The CDP debug port is forwarded to WSL via the mirrored-networking
  localhost (networkingMode=mirrored in .wslconfig), so
  http://localhost:<port>/json/version is directly reachable from WSL.
- CDPMuxProxy wraps the port so multiple browser-use CLI processes can
  share the same Chrome instance, one tab per task (same pattern as
  ZiniaoBackend).

Requirements on the Windows side
---------------------------------
- Google Chrome installed (checked at start time).
- WSL2 in mirrored networking mode (networkingMode=mirrored in .wslconfig).
- Auto-login configured (so an interactive Windows session always exists
  for Task Scheduler to place Chrome in).

Per-store config (browser_config dict, set by BrowserManager)
--------------------------------------------------------------
  debug_port   int   CDP port Chrome listens on  (default: 9223)
  store_slug   str   Used for Task Scheduler task name and profile dir
  proxy_port   int   Local CDPMuxProxy listen port (default: 9222)
"""

import asyncio
import logging
import os
import pathlib
import socket
import subprocess
import time

import aiohttp

from app.browser.base import BrowserBackend, BrowserSessionInfo
from app.browser.cdp_mux_proxy import CDPMuxProxy
from app.config import DOWNLOADS_DIR, LOCALHOST

logger = logging.getLogger(__name__)

# Chrome executable paths to probe on Windows (via /mnt/c in WSL).
_CHROME_CANDIDATES = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
]
_CHROME_CANDIDATES_WSL = [
    '/mnt/c/Program Files/Google/Chrome/Application/chrome.exe',
    '/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe',
]

# Windows profile base dir for per-store Chrome profiles.
_PROFILE_BASE_WIN = r'C:\Users\Administrator\AppData\Local\vibe-seller-chrome'

# Windows download base dir. Native Windows Chrome can only write
# downloads to a Windows path (C:\...), so per-store downloads live here
# and are surfaced to WSL agents via a symlink (see _ensure_download_link).
_DOWNLOADS_BASE_WIN = rf'{_PROFILE_BASE_WIN}\downloads'

_TASK_PREFIX = 'VibeSeller-Chrome-'

_CDP_STARTUP_TIMEOUT = 15  # seconds to wait for Chrome CDP after launch
_CDP_POLL_INTERVAL = 0.5


def _is_wsl() -> bool:
    try:
        version = pathlib.Path('/proc/version').read_text(encoding='utf-8')
        return 'microsoft' in version.lower() or 'wsl' in version.lower()
    except OSError:
        return False


def _find_chrome_win_path() -> str | None:
    """Return the first existing Windows Chrome path (as a Windows path)."""
    for wsl_path, win_path in zip(
        _CHROME_CANDIDATES_WSL, _CHROME_CANDIDATES, strict=False
    ):
        if os.path.isfile(wsl_path):
            return win_path
    return None


def _powershell(cmd: str, timeout: int = 30) -> str:
    """Run a PowerShell command via WSL interop, return stdout."""
    result = subprocess.run(
        [
            '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe',
            '-NonInteractive',
            '-NoProfile',
            '-Command',
            cmd,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd='/mnt/c/',
    )
    return result.stdout.strip()


def _task_exists(task_name: str) -> bool:
    out = _powershell(
        f"(Get-ScheduledTask -TaskName '{task_name}' "
        f'-ErrorAction SilentlyContinue) -ne $null'
    )
    return out.strip().lower() == 'true'


def _create_chrome_task(
    task_name: str, chrome_path: str, debug_port: int, profile_dir: str
) -> None:
    """Create a Task Scheduler task that launches Chrome in the interactive session."""
    args = (
        f'--remote-debugging-port={debug_port} '
        f'--no-first-run '
        f'--no-default-browser-check '
        f'--disable-popup-blocking '
        f'--disable-infobars '
        f'--user-data-dir={profile_dir}'
    )
    ps = f"""
$action   = New-ScheduledTaskAction -Execute '{chrome_path}' -Argument '{args}'
$trigger  = New-ScheduledTaskTrigger -AtLogon
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -MultipleInstances IgnoreNew
$principal= New-ScheduledTaskPrincipal -UserId (whoami) -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName '{task_name}' -Action $action `
    -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Output 'created'
"""
    out = _powershell(ps.strip())
    logger.info('Created Task Scheduler task %s: %s', task_name, out)


def _start_chrome_task(task_name: str) -> None:
    _powershell(f"Start-ScheduledTask -TaskName '{task_name}'")
    logger.info('Started Task Scheduler task %s', task_name)


def _chrome_running_on_port(port: int) -> bool:
    """Quick sync check — Chrome CDP responds on localhost:<port>."""
    try:
        with socket.create_connection(('localhost', port), timeout=1):
            return True
    except OSError:
        return False


def _win_to_wsl(win_path: str) -> str:
    """Translate a Windows path (``C:\\...``) to its /mnt drive view in WSL."""
    out = subprocess.run(
        ['wslpath', '-u', win_path],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return out.stdout.strip()


def _ensure_download_link(
    std_dir: pathlib.Path, wsl_target: pathlib.Path
) -> None:
    """Point the standard per-store download dir at the Windows-backed dir.

    Native Windows Chrome writes downloads to ``wsl_target`` (a /mnt/c
    path it can reach as ``C:\\...``). Agents read the platform-standard
    ``~/.vibe-seller/downloads/<slug>`` path, so symlink the latter to the
    former. Any pre-existing files in a real dir are migrated first.
    """
    std_dir.parent.mkdir(parents=True, exist_ok=True)
    if std_dir.is_symlink():
        if std_dir.resolve() == wsl_target.resolve():
            return
        std_dir.unlink()
    elif std_dir.exists():
        for f in std_dir.iterdir():
            dest = wsl_target / f.name
            if not dest.exists():
                f.rename(dest)
        try:
            std_dir.rmdir()
        except OSError:
            logger.warning(
                'Download dir %s not empty after migration; leaving as a '
                'real dir — Chrome downloads will land in %s instead',
                std_dir,
                wsl_target,
            )
            return
    std_dir.symlink_to(wsl_target)


async def _wait_for_cdp(
    port: int, timeout: float = _CDP_STARTUP_TIMEOUT
) -> None:
    deadline = time.monotonic() + timeout
    url = f'http://{LOCALHOST}:{port}/json/version'
    async with aiohttp.ClientSession() as session:
        while time.monotonic() < deadline:
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=2)
                ) as resp:
                    if resp.status == 200:
                        logger.info('Chrome CDP ready on port %d', port)
                        return
            except Exception:
                pass
            await asyncio.sleep(_CDP_POLL_INTERVAL)
    raise RuntimeError(
        f'Chrome did not expose CDP on localhost:{port} '
        f'within {timeout}s. '
        f'Make sure Chrome is installed on Windows and an interactive '
        f'Windows session is active (auto-login configured).'
    )


class WinChromeBackend(BrowserBackend):
    """Launch Windows-native Chrome via Task Scheduler, connect via CDP."""

    def __init__(self):
        self._proxy: CDPMuxProxy | None = None

    async def start(self, browser_config: dict) -> BrowserSessionInfo:
        if not _is_wsl():
            raise RuntimeError(
                'WinChromeBackend only works inside WSL2 on Windows. '
                'Use ChromeBackend on macOS/Linux.'
            )

        debug_port = int(browser_config.get('debug_port', 9223))
        proxy_port = int(browser_config.get('proxy_port', 9222))
        store_slug = browser_config.get('store_slug', 'default')

        task_name = f'{_TASK_PREFIX}{store_slug}'
        profile_dir = rf'{_PROFILE_BASE_WIN}\{store_slug}'

        # Ensure Chrome is installed on the Windows side.
        chrome_path = _find_chrome_win_path()
        if not chrome_path:
            raise RuntimeError(
                'Google Chrome not found on Windows. '
                'Install Chrome at the default location and retry. '
                f'Expected: {_CHROME_CANDIDATES[0]}'
            )

        # Create the Task Scheduler task if it doesn't exist yet.
        if not _task_exists(task_name):
            logger.info(
                'Creating Task Scheduler task %s (port=%d)',
                task_name,
                debug_port,
            )
            _create_chrome_task(task_name, chrome_path, debug_port, profile_dir)

        # Launch Chrome if not already running.
        if not _chrome_running_on_port(debug_port):
            logger.info(
                'Chrome not running on port %d — starting task %s',
                debug_port,
                task_name,
            )
            _start_chrome_task(task_name)
            await _wait_for_cdp(debug_port)
        else:
            logger.info('Chrome already running on port %d', debug_port)

        # Per-store download dir. Native Windows Chrome can only write to a
        # Windows path, so downloads land in a /mnt/c-backed dir: Chrome
        # writes via the Windows form (handed to CDP setDownloadBehavior),
        # the agent reads via the standard ~/.vibe-seller/downloads/<slug>
        # path, symlinked to the Windows-backed dir.
        dl_win = rf'{_DOWNLOADS_BASE_WIN}\{store_slug}'
        dl_wsl = pathlib.Path(_win_to_wsl(dl_win))
        dl_wsl.mkdir(parents=True, exist_ok=True)
        _ensure_download_link(DOWNLOADS_DIR / store_slug, dl_wsl)

        # Wrap with CDPMuxProxy so multiple tasks share one Chrome. The mux
        # hands `download_dir` straight to CDP, so it must be the Windows
        # path string (not the WSL view) for native Chrome to honor it.
        self._proxy = CDPMuxProxy(
            listen_port=proxy_port,
            target_port=debug_port,
            target_host=LOCALHOST,
            download_dir=dl_win,
            keep_last_page=False,
        )
        await self._proxy.start()

        logger.info(
            'WinChrome ready: proxy=%d -> CDP localhost:%d (store=%s)',
            proxy_port,
            debug_port,
            store_slug,
        )
        return BrowserSessionInfo(cdp_port=debug_port)

    async def stop(self, info: BrowserSessionInfo) -> None:
        try:
            if self._proxy:
                await self._proxy.stop()
                self._proxy = None
        except Exception as e:
            logger.warning('Error stopping WinChromeBackend: %s', e)
