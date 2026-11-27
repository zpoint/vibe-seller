"""BitBrowser launch + discovery: the client's own settings, and how
to find and start the exe/bundle on Windows, WSL and macOS.

Split out of ``bitbrowser`` (the client + spec) per the vendor-module
convention for large adapters. Everything here is either a blocking
helper (called through ``asyncio.to_thread`` by the client) or an
async discovery reader:

- the client's ``config.json`` (``%APPDATA%\\BitBrowser`` on
  windows/wsl, ``~/Library/Application Support/BitBrowser`` on mac)
  holds the local API port and, when the user turned 鉴权控制 on,
  the API token — read from there, never stored by us;
- the Windows exe is located through the shared
  :mod:`app.browser.win_app_locator` (the install dir descriptor
  lives on the vendor spec); the macOS bundle through the shared
  :mod:`app.browser.mac_app_locator`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from urllib.parse import urlsplit

from app.browser import mac_app_locator as _mac_locator
from app.browser.win_app_locator import run_powershell, win_path_to_local
from app.browser.ziniao_utils import get_platform

logger = logging.getLogger(__name__)

# The client's own settings file (written by BitBrowser itself):
# {"localServerAddress": "http://127.0.0.1:54345",
#  "enableAuth": false, "authToken": "<hex>"}   (authToken when on)
_CONFIG_SUBPATH = os.path.join('BitBrowser', 'config.json')

# The local API binds 0.0.0.0 but is only ever driven on loopback; the
# config's host must be loopback before we dial it.
_LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})

_APPDATA_CACHE: str | None = None
_APPDATA_LOCK = asyncio.Lock()


async def _windows_appdata() -> str | None:
    """The Windows ``%APPDATA%`` dir as a Windows-style path.

    Native Windows: the environment. WSL driving the Windows side:
    asked of Windows itself via PowerShell (``GetFolderPath``), the
    same technique ``win_app_locator`` uses for the shortcut folders;
    resolved once and cached.
    """
    global _APPDATA_CACHE
    env = os.environ.get('APPDATA')
    if env:
        return env
    if _APPDATA_CACHE is not None:
        return _APPDATA_CACHE
    async with _APPDATA_LOCK:
        if _APPDATA_CACHE is None:
            _APPDATA_CACHE = await asyncio.to_thread(
                lambda: (
                    run_powershell(
                        "[Environment]::GetFolderPath('ApplicationData')"
                    ).strip()
                    or ''
                )
            )
    return _APPDATA_CACHE or None


def _read_config_file(path: str) -> dict:
    """Parse a config.json (blocking; {} on a missing or garbage file —
    a client that never ran, or an old version without the file, must
    not break the default-port flow). A fresh macOS install's file
    holds only ``localServerAddress`` — a missing ``enableAuth`` key
    simply means auth off.
    """
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        logger.info('bitbrowser config %s unreadable: %s', path, e)
        return {}
    return data if isinstance(data, dict) else {}


def _mac_config_path() -> str:
    """``~/Library/Application Support/BitBrowser/config.json`` (the
    renderer's ``--user-data-dir`` confirms the data dir)."""
    return os.path.expanduser(
        os.path.join(
            '~', 'Library', 'Application Support', 'BitBrowser', 'config.json'
        )
    )


async def read_client_config() -> dict:
    """The client's config.json, per platform ({} when unavailable).

    ``mac`` reads the macOS data dir directly; ``windows`` uses the
    environment; ``wsl`` asks Windows itself (PowerShell
    ``GetFolderPath``, the win_app_locator technique) and opens the
    file through ``/mnt``.
    """
    if get_platform() == 'mac':
        return await asyncio.to_thread(_read_config_file, _mac_config_path())
    appdata = await _windows_appdata()
    if not appdata:
        return {}
    return await asyncio.to_thread(
        _read_config_file,
        win_path_to_local(os.path.join(appdata, _CONFIG_SUBPATH)),
    )


def port_from_address(address: object) -> int | None:
    """The port of a ``http://127.0.0.1:54345`` localServerAddress.

    Only loopback hosts are honoured (the API is a local API; a config
    pointing elsewhere is not ours to dial).
    """
    if not isinstance(address, str) or '://' not in address:
        return None
    parts = urlsplit(address)
    if (parts.hostname or '').lower() not in _LOOPBACK_HOSTS:
        return None
    if parts.port and 0 < parts.port < 65536:
        return parts.port
    return None


def launch_exe(exe_path: str) -> bool:
    """Launch the client detached (UTF-8 both sides, so the Chinese
    exe path survives any console codepage — the run_powershell
    contract)."""
    quoted = exe_path.strip().strip('"').replace("'", "''")
    return (
        run_powershell(
            f"try {{ Start-Process -FilePath '{quoted}'; 'ok' }} "
            "catch { 'fail' }"
        )
        .strip()
        .endswith('ok')
    )


# ── the macOS descriptor (shared locator's fixed interface) ──────────
#
# Values probed live on the Mac (~/bitbrowser-glm/mac/MAC-FACTS.md):
# the client bundle is Chinese-named 比特浏览器.app with bundle id
# com.bitnet.bitbrowser. The profile KERNEL is a different, ENGLISH-
# named bundle (Chrome-bin/<ver>/BitBrowser.app, Firefox-bin/<ver>/
# BitFox.app) inside the data dir — it must never be matched as the
# client (app_names stays Chinese-only; the launch path refuses
# Chrome-bin/Firefox-bin paths).
_MAC_BUNDLE_IDS = ('com.bitnet.bitbrowser',)
_MAC_APP_NAMES = ('比特浏览器.app',)


def mac_app_descriptor():
    """The BitBrowser MacAppSpec for the shared macOS locator — the
    ONE place that constructs it."""
    return _mac_locator.MacAppSpec(
        key='bitbrowser',
        display_name='比特浏览器 (BitBrowser)',
        bundle_ids=_MAC_BUNDLE_IDS,
        app_names=_MAC_APP_NAMES,
    )
