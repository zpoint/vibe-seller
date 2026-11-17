"""The OS system proxy, handed to the commands that download for us.

A user behind the GFW runs Clash / V2Ray and switches on the OS "system
proxy". Browsers follow that setting; ``git``, ``uv``, ``npm`` and
Playwright's downloader do not, they read only the ``*_proxy`` env
vars. Spawned bare, such a command goes out over the direct network and
sits on its first connection with no output. ``install.sh`` does the
same translation for the installer (``setup_proxy``); this is the one
Python copy for the server's own downloads.

Use ``download_env()`` as the ``env=`` of any subprocess that fetches
from the network. Proxy vars the user exported win; with none exported
the OS proxy is translated, and with neither the env passes unchanged.
"""

import os
import urllib.request

PROXY_VARS = (
    'https_proxy',
    'HTTPS_PROXY',
    'http_proxy',
    'HTTP_PROXY',
    'all_proxy',
    'ALL_PROXY',
)


def proxy_env() -> dict[str, str]:
    """Proxy vars to add on top of ``os.environ``, or ``{}``.

    Empty when the user already exported one (theirs wins) or the OS
    has no proxy. ``urllib.request.getproxies`` reads the env first and
    falls back to the OS settings (Windows Internet Settings, macOS
    network preferences), so with the env empty it is the OS proxy.
    """
    if any(os.environ.get(v) for v in PROXY_VARS):
        return {}
    system = urllib.request.getproxies()
    proxy = system.get('https') or system.get('http')
    if not proxy:
        return {}
    if '://' not in proxy:
        proxy = 'http://' + proxy
    return {'HTTPS_PROXY': proxy, 'HTTP_PROXY': proxy}


def download_env() -> dict[str, str]:
    """``os.environ`` plus the system proxy: ``env=`` for a download."""
    return {**os.environ, **proxy_env()}
