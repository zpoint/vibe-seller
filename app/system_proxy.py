"""The proxy a download should take, decided before the download starts.

A user behind the GFW runs Clash / V2Ray and switches on the OS "system
proxy". Browsers follow that setting; ``git``, ``uv``, ``pip``, ``npm``
and Playwright's downloader do not, they read only the ``*_proxy`` env
vars. Spawned bare, such a command goes out over the direct network and
sits on its first connection with no output. The opposite failure is
as common: a ``HTTPS_PROXY`` left in the env by an old install guide
long after the proxy stopped, so every download dies on it.

``install.sh`` settles this once for the installer (``setup_proxy``);
this is the Python side, for the downloads the server and the CLI start
themselves:

- ``download_env()`` translates the proxy (exported vars win, else the
  OS setting) into env vars. No check.
- ``checked_download_env(targets)`` first asks whether that proxy is
  alive and reaches every target (GitHub, PyPI, ...). It does: the env
  carries the proxy. It does not: the env carries NO proxy var at all
  and the download goes direct — a proxy that cannot serve the download
  is never forced on it.

Use the checked one for a download whose targets you know; the answer
is cached for a minute, so the next download in a burst does not pay
for the probes again. A proxy that is not http(s) (``socks5://``)
cannot be probed with urllib and is trusted as set.
"""

import asyncio
import http.client
import logging
import os
import time
import urllib.error
from urllib.parse import urlsplit
import urllib.request

logger = logging.getLogger(__name__)

PROXY_VARS = (
    'https_proxy',
    'HTTPS_PROXY',
    'http_proxy',
    'HTTP_PROXY',
    'all_proxy',
    'ALL_PROXY',
)

GITHUB = 'https://github.com'
PYPI = 'https://pypi.org/simple/'
NPM = 'https://registry.npmjs.org/'

PROBE_TIMEOUT_S = 6.0
_CACHE_TTL_S = 60.0

# (proxy or None, url) -> (when, reached). Process-wide, small.
_reach_cache: dict[tuple[str | None, str], tuple[float, bool]] = {}


def redact_proxy_url(url: str) -> str:
    """Scheme, host and port only: a proxy URL can carry a
    ``user:password@``, and this is logged and shown to users."""
    parts = urlsplit(url if '://' in url else f'http://{url}')
    host = parts.hostname or '?'
    if ':' in host:
        host = f'[{host}]'
    try:
        port = parts.port
    except ValueError:
        port = None
    return f'{parts.scheme}://{host}' + (f':{port}' if port else '')


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
    """``os.environ`` plus the system proxy, unchecked."""
    return {**os.environ, **proxy_env()}


def _env_proxy() -> str | None:
    for var in PROXY_VARS:
        if os.environ.get(var):
            return os.environ[var]
    return None


def candidate_proxy() -> str | None:
    """The proxy a download would take: exported, else the OS's."""
    env = _env_proxy()
    if env:
        return env
    return proxy_env().get('HTTPS_PROXY')


def _probe(url: str, proxy: str | None) -> bool:
    """Whether *url* answers through *proxy* (None = direct).

    Any HTTP answer counts, a 403 or 404 included: the server was
    reached. Only a connection that fails or stalls does not.
    """
    handlers = {'http': proxy, 'https': proxy} if proxy else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(handlers))
    request = urllib.request.Request(url, method='HEAD')
    try:
        opener.open(request, timeout=PROBE_TIMEOUT_S).close()
    except urllib.error.HTTPError:
        return True
    except (OSError, http.client.HTTPException, ValueError):
        return False
    return True


def reaches(url: str, proxy: str | None) -> bool:
    """``_probe`` with a one-minute memory."""
    key = (proxy, url)
    hit = _reach_cache.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < _CACHE_TTL_S:
        return hit[1]
    ok = _probe(url, proxy)
    _reach_cache[key] = (now, ok)
    return ok


def _direct_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in PROXY_VARS}


def _probeable(proxy: str) -> bool:
    return urlsplit(proxy if '://' in proxy else f'http://{proxy}').scheme in (
        'http',
        'https',
    )


def checked_download_env(
    targets: tuple[str, ...] = (GITHUB, PYPI),
) -> dict[str, str]:
    """``env=`` for a download from *targets*, proxy decided by probing.

    No proxy anywhere: the env as is. A proxy that is alive and reaches
    every target: the env with that proxy. Otherwise the env with every
    proxy var removed, so the download goes direct instead of dying on
    a proxy that cannot serve it.
    """
    proxy = candidate_proxy()
    if proxy is None:
        return dict(os.environ)
    if not _probeable(proxy):
        return download_env()
    blocked = [t for t in targets if not reaches(t, proxy)]
    if not blocked:
        return download_env()
    logger.warning(
        'Proxy %s does not reach %s — downloading over the direct network',
        redact_proxy_url(proxy),
        ', '.join(urlsplit(t).hostname or t for t in blocked),
    )
    return _direct_env()


async def checked_download_env_async(
    targets: tuple[str, ...] = (GITHUB, PYPI),
) -> dict[str, str]:
    """``checked_download_env`` off the event loop (it makes requests)."""
    return await asyncio.to_thread(checked_download_env, targets)


def pypi_target(env: dict[str, str] | None = None) -> str:
    """The Python package index a ``pip``/``uv`` install will ask.

    The index the user configured (a mirror), else PyPI: probing
    pypi.org for someone who installs from a mirror would blame the
    proxy for a site the install never visits.
    """
    env = os.environ if env is None else env
    for var in ('UV_DEFAULT_INDEX', 'UV_INDEX_URL', 'PIP_INDEX_URL'):
        if env.get(var):
            return env[var]
    return PYPI


def npm_target(env: dict[str, str] | None = None) -> str:
    """The registry ``npm install`` will ask: configured, else npmjs."""
    env = os.environ if env is None else env
    return (
        env.get('npm_config_registry') or env.get('NPM_CONFIG_REGISTRY') or NPM
    )
