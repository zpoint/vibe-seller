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

Use the checked one for a download whose targets you know; a yes is
cached for a minute (a no for seconds), so the next download in a
burst does not pay for the probes again. ``decide_download_env`` also
says what was decided, for an error message that tells the truth. A
proxy that is not http(s) (``socks5://``) cannot be probed with urllib
and is trusted as set.

A download made in-process (urllib, httpx) has the same two failures
the other way round: it does follow the OS proxy, but never asks whether
it, or an exported one, works. ``Decision.client_proxies`` is the same
answer for such a client, and ``urlopen`` applies it.
"""

import asyncio
import dataclasses
import http.client
import logging
import os
import ssl
import sys
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
_NEGATIVE_TTL_S = 10.0

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
    proxy = _system_proxy()
    if not proxy:
        return {}
    return {'HTTPS_PROXY': proxy, 'HTTP_PROXY': proxy}


def _platform_proxies() -> dict[str, str]:
    """The OS proxy settings alone, whatever the env says."""
    if sys.platform == 'darwin':
        return urllib.request.getproxies_macosx_sysconf()
    if os.name == 'nt':
        return urllib.request.getproxies_registry()  # type: ignore[attr-defined]
    return {}


def _system_proxy() -> str | None:
    """The OS's proxy as a URL, or None. With no proxy var exported,
    ``getproxies`` IS the OS setting; with one exported it would echo
    the env back, so the OS is asked directly."""
    if any(os.environ.get(v) for v in PROXY_VARS):
        system = _platform_proxies()
    else:
        system = urllib.request.getproxies()
    proxy = system.get('https') or system.get('http')
    if not proxy:
        return None
    return proxy if '://' in proxy else 'http://' + proxy


def download_env() -> dict[str, str]:
    """``os.environ`` plus the system proxy, unchecked."""
    return {**os.environ, **proxy_env()}


# The vars an https download reads, in curl's order. ``http_proxy`` is
# not one: curl, git, uv and pip use it for plain-http URLs only.
_HTTPS_PROXY_VARS = ('https_proxy', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY')


def _env_proxy() -> str | None:
    for var in _HTTPS_PROXY_VARS:
        if os.environ.get(var):
            return os.environ[var]
    return None


def candidate_proxy() -> str | None:
    """The proxy an https download would take: exported, else the OS's.

    None when the only exported var is ``http_proxy``: the download
    will not use it for https, so there is nothing to decide.
    """
    return _env_proxy() or proxy_env().get('HTTPS_PROXY')


def _origin(url: str) -> str:
    """``scheme://host[:port]/`` of *url*: no credentials, no path.

    A mirror URL can carry ``user:token@`` (which urllib rejects as a
    bad port) and a path that needs auth; reaching the host is the
    question.
    """
    parts = urlsplit(url)
    host = parts.hostname or ''
    if ':' in host:
        host = f'[{host}]'
    try:
        port = parts.port
    except ValueError:
        port = None
    return f'{parts.scheme}://{host}' + (f':{port}' if port else '') + '/'


# What a proxy answers when IT could not reach the target, for a
# plain-http URL (an https URL gets these as a failed CONNECT instead).
_PROXY_GATEWAY_ERRORS = (502, 503, 504)


def _probe(url: str, proxy: str | None) -> bool:
    """Whether *url* answers through *proxy* (None = direct).

    Any HTTP answer from the server counts, a 403 or 404 included: it
    was reached. Not reached: a connection that fails or stalls, or a
    proxy saying it could not get there. Reached although urllib fails:
    a proxy asking for auth (407; the download's own client may do
    NTLM/Kerberos) and a certificate this Python does not trust (a
    TLS-inspecting corporate proxy — the download has its own CA store).
    A handshake that is cut off is NOT a certificate problem; that is
    what a blocked connection looks like.
    """
    handlers = {'http': proxy, 'https': proxy} if proxy else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(handlers))
    request = urllib.request.Request(url, method='HEAD')
    try:
        opener.open(request, timeout=PROBE_TIMEOUT_S).close()
    except urllib.error.HTTPError as e:
        return not (proxy and e.code in _PROXY_GATEWAY_ERRORS)
    except urllib.error.URLError as e:
        return isinstance(e.reason, ssl.SSLCertVerificationError) or (
            bool(proxy) and ' 407' in str(e.reason)
        )
    except ssl.SSLCertVerificationError:
        return True
    except OSError as e:
        # A refused CONNECT: "Tunnel connection failed: 407 ...".
        return bool(proxy) and ' 407' in str(e)
    except (http.client.HTTPException, ValueError):
        return False
    return True


def reaches(url: str, proxy: str | None) -> bool:
    """``_probe`` of *url*'s host, remembered.

    A yes for a minute; a no only briefly, so a retry after the proxy
    came up (or a one-off stall) asks again.
    """
    key = (proxy, _origin(url))
    hit = _reach_cache.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < (
        _CACHE_TTL_S if hit[1] else _NEGATIVE_TTL_S
    ):
        return hit[1]
    ok = _probe(key[1], proxy)
    _reach_cache[key] = (now, ok)
    return ok


def _direct_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in PROXY_VARS}


def _probeable(proxy: str) -> bool:
    return urlsplit(proxy if '://' in proxy else f'http://{proxy}').scheme in (
        'http',
        'https',
    )


@dataclasses.dataclass(frozen=True)
class Decision:
    """The ``env=`` for a download, and how it was arrived at."""

    env: dict[str, str]
    proxy: str | None = None  # the proxy the download takes, if any
    # Proxies tried and passed over, each with the hosts it missed.
    failed: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # The same decision for an in-process client (urllib, httpx), as a
    # ``{'http': ..., 'https': ...}`` map; ``{}`` is direct. None when
    # the client's own reading of the env already lands on it (the
    # exported proxy won, or there was nothing to decide) — so a run
    # with no proxy anywhere is exactly what it was before.
    client_proxies: dict[str, str] | None = None

    @property
    def dropped(self) -> bool:
        """A proxy was found and none was used: the download goes direct."""
        return self.proxy is None and bool(self.failed)

    def describe(self) -> str:
        """For an error message: which path the download took."""
        misses = '; '.join(
            f'proxy {redact_proxy_url(p)} could not reach {", ".join(h)}'
            for p, h in self.failed
        )
        if self.dropped:
            return f'{misses}, so the direct network was used'
        if self.proxy:
            via = f'through proxy {redact_proxy_url(self.proxy)}'
            return f'{via} ({misses})' if misses else via
        return 'no proxy configured'


def decide_download_env(
    targets: tuple[str, ...] = (GITHUB, PYPI),
) -> Decision:
    """``env=`` for a download from *targets*, proxy decided by probing.

    Candidates in order: the exported proxy, then the OS's. The first
    one that is alive and reaches every target is used; the exported
    one as set, the OS one as ``HTTPS_PROXY``/``HTTP_PROXY`` (with any
    other proxy var removed). A stale export therefore no longer hides
    a working system proxy. None works: the env with every proxy var
    removed, so the download goes direct. No proxy anywhere, or no
    http(s) target (a ``file://`` source in a test): the env as is.
    """
    targets = tuple(
        t for t in targets if urlsplit(t).scheme in ('http', 'https')
    )
    if not targets:
        return Decision(dict(os.environ))
    exported = _env_proxy()
    candidates = [p for p in (exported, _system_proxy()) if p]
    failed: list[tuple[str, tuple[str, ...]]] = []
    for proxy in dict.fromkeys(candidates):
        if _probeable(proxy):
            blocked = tuple(
                urlsplit(t).hostname or t
                for t in targets
                if not reaches(t, proxy)
            )
            if blocked:
                failed.append((proxy, blocked))
                continue
        client: dict[str, str] | None = None
        if proxy == exported:
            env = download_env()
        else:
            env = {**_direct_env(), 'HTTPS_PROXY': proxy, 'HTTP_PROXY': proxy}
            client = {'http': proxy, 'https': proxy}
        if failed:
            logger.warning(
                'Proxy %s does not reach %s — using %s',
                redact_proxy_url(failed[0][0]),
                ', '.join(failed[0][1]),
                redact_proxy_url(proxy),
            )
        return Decision(env, proxy, tuple(failed), client)
    if not failed:
        return Decision(dict(os.environ))
    logger.warning(
        'No proxy reaches %s (%s) — downloading over the direct network',
        ', '.join(failed[-1][1]),
        ', '.join(redact_proxy_url(p) for p, _ in failed),
    )
    return Decision(_direct_env(), None, tuple(failed), {})


def urlopen(request: str | urllib.request.Request, timeout: float):
    """``urllib.request.urlopen`` over the network path
    ``decide_download_env`` picks for the request's host.

    The plain ``urlopen`` reads the env first and the OS proxy only when
    the env has none, and never asks whether either works: a stale
    ``HTTPS_PROXY`` sinks the request, and so does a system proxy that
    cannot reach the host. When there is nothing to correct this IS
    the plain ``urlopen`` (same opener, same handlers).
    """
    url = request if isinstance(request, str) else request.full_url
    proxies = decide_download_env((url,)).client_proxies
    if proxies is None:
        return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    return opener.open(request, timeout=timeout)


def checked_download_env(
    targets: tuple[str, ...] = (GITHUB, PYPI),
) -> dict[str, str]:
    """``decide_download_env(targets).env``."""
    return decide_download_env(targets).env


async def decide_download_env_async(
    targets: tuple[str, ...] = (GITHUB, PYPI),
) -> Decision:
    """``decide_download_env`` off the event loop (it makes requests)."""
    return await asyncio.to_thread(decide_download_env, targets)


async def checked_download_env_async(
    targets: tuple[str, ...] = (GITHUB, PYPI),
) -> dict[str, str]:
    """``checked_download_env`` off the event loop (it makes requests)."""
    return (await decide_download_env_async(targets)).env


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
