"""Vibe Seller system-tray launcher for native Windows.

Ollama-style UX: this runs on login (registered in the Startup
folder by the installer), starts the Vibe Seller server in the
background, and shows a tray icon with Open UI / Restart / Quit.

It is intentionally thin — all server lifecycle goes through the
existing ``vibe-seller`` CLI (``start`` daemonises via subprocess,
``stop`` tears the daemon down), so the tray never owns process
management itself. That keeps one code path for daemon start/stop
across every platform.

Launched via the bundled ``pythonw.exe`` (no console window):

    pythonw.exe tray.py

Dependencies (pystray, Pillow) ship in the installer's wheel bundle
and are installed into the runtime venv at install time — they are
NOT runtime dependencies of the core package.
"""

from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from PIL import Image, ImageDraw
import pystray

from app import windows_update
from app.version import get_version

PORT = int(os.environ.get('VIBE_SELLER_PORT', '7777'))
URL = f'http://127.0.0.1:{PORT}'
HEALTH_URL = f'{URL}/api/health'

# The installer sets VIBE_SELLER_HOME to the install dir; fall back to
# this file's parent so a dev run still works.
INSTALL_DIR = Path(os.environ.get('VIBE_SELLER_HOME', Path(__file__).parent))
LOG_PATH = (
    Path(os.environ.get('LOCALAPPDATA', Path.home()))
    / 'vibe-seller'
    / 'logs'
    / 'tray.log'
)

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)
logger = logging.getLogger('vibe-seller-tray')


def _vibe_seller_exe() -> str:
    """Path to the bundled venv's ``vibe-seller`` console script."""
    candidate = INSTALL_DIR / '.venv' / 'Scripts' / 'vibe-seller.exe'
    if candidate.exists():
        return str(candidate)
    # Dev fallback: rely on PATH.
    return 'vibe-seller'


def _run_cli(*args: str) -> int:
    """Invoke the vibe-seller CLI, no console window."""
    flags = 0
    if sys.platform == 'win32':
        flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        proc = subprocess.run(
            [_vibe_seller_exe(), *args],
            creationflags=flags,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning(
                'vibe-seller %s exited %d: %s',
                ' '.join(args),
                proc.returncode,
                proc.stderr.strip(),
            )
        return proc.returncode
    except (OSError, subprocess.SubprocessError):
        logger.exception('Failed to run vibe-seller %s', ' '.join(args))
        return 1


def _augment_path() -> None:
    """Prepend the bundled toolchain to PATH for the daemon.

    The daemon (and its children) must find, by name:
    - ``uv`` — the workspace manager runs ``uv venv``/``uv pip`` to
      build the agent venv (bundled at the install root as uv.exe)
    - ``browser-use`` / ``playwright`` — in the venv's Scripts dir;
      the per-store wrapper resolves ``shutil.which('browser-use')``
    - ``claude`` — the agent CLI
    - ``git`` / ``bash`` — MinGit; Claude Code runs its Bash tool
      through Git Bash on Windows

    The daemon inherits os.environ, so prepending here is enough.
    """
    extra = [
        INSTALL_DIR / '.venv' / 'Scripts',
        INSTALL_DIR,
        INSTALL_DIR / 'claude',
        INSTALL_DIR / 'mingit' / 'cmd',
        INSTALL_DIR / 'mingit' / 'usr' / 'bin',
    ]
    dirs = [str(p) for p in extra if p.is_dir()]
    if dirs:
        os.environ['PATH'] = os.pathsep.join([
            *dirs,
            os.environ.get('PATH', ''),
        ])
        logger.info('PATH augmented with bundled tools: %s', dirs)


def _server_healthy(timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _start_server() -> None:
    if _server_healthy():
        logger.info('Server already healthy on %s', URL)
        return
    logger.info('Starting server: vibe-seller start --port %d', PORT)
    _run_cli('start', '--port', str(PORT))


def _stop_server() -> None:
    logger.info('Stopping server: vibe-seller stop --port %d', PORT)
    _run_cli('stop', '--port', str(PORT))


# -- tray icon --------------------------------------------------------


def _icon_image() -> Image.Image:
    """The shared brand icon (bundled vibe-seller.ico) so the tray
    matches the web favicon; a drawn fallback if the asset is absent."""
    ico = INSTALL_DIR / 'vibe-seller.ico'
    if ico.is_file():
        try:
            return Image.open(ico)
        except OSError:
            pass
    img = Image.new('RGB', (64, 64), (99, 102, 241))
    draw = ImageDraw.Draw(img)
    draw.line(
        [(18, 16), (32, 48), (46, 16)],
        fill=(255, 255, 255),
        width=6,
        joint='curve',
    )
    return img


def _on_open(icon, item):  # noqa: ARG001
    webbrowser.open(URL)


def _on_restart(icon, item):  # noqa: ARG001
    _stop_server()
    time.sleep(1.0)
    _start_server()


def _msgbox(title: str, text: str) -> None:
    if sys.platform == 'win32':
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    else:
        logger.info('%s: %s', title, text)


def _on_check_updates(icon, item):  # noqa: ARG001
    res = windows_update.upgrade(silent=False)
    status = res.get('status')
    if status == 'up-to-date':
        _msgbox(
            'Vibe Seller',
            f"You're on the latest version ({res.get('version')}).",
        )
    elif status == 'updating':
        _msgbox(
            'Vibe Seller',
            f'Downloading v{res.get("version")} — the installer will '
            'open to finish the update.',
        )
    else:
        _msgbox(
            'Vibe Seller — update failed',
            f'{res.get("error")}\n\nDownload manually:\n'
            f'{res.get("manual_url")}',
        )


def _on_quit(icon, item):  # noqa: ARG001
    _stop_server()
    icon.stop()


def main() -> int:
    # Start the server in the background, then bring up the tray.
    _augment_path()
    # Reuse the bundled interpreter for the agent venv instead of
    # letting `uv venv` download a second Python. The daemon inherits
    # this env var.
    bundled_py = INSTALL_DIR / 'python' / 'python.exe'
    if bundled_py.is_file():
        os.environ['VIBE_SELLER_BUNDLED_PYTHON'] = str(bundled_py)
    threading.Thread(target=_start_server, daemon=True).start()

    try:
        title = f'Vibe Seller {get_version()}'
    except Exception:  # noqa: BLE001 — tooltip is cosmetic
        title = 'Vibe Seller'
    icon = pystray.Icon(
        'vibe-seller',
        _icon_image(),
        title,
        menu=pystray.Menu(
            pystray.MenuItem('Open Vibe Seller', _on_open, default=True),
            pystray.MenuItem('Restart server', _on_restart),
            pystray.MenuItem('Check for updates', _on_check_updates),
            pystray.MenuItem('Quit', _on_quit),
        ),
    )
    logger.info('Tray starting (install_dir=%s, port=%d)', INSTALL_DIR, PORT)
    icon.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
