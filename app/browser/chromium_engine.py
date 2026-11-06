"""Keep the Chromium the running Playwright expects on disk.

The chrome backend (and the store-less ``web`` browser) launch
Playwright's own Chromium build, and every Playwright release pins its
own revision (``ms-playwright/chromium-<rev>``). Install steps put one
there — ``install.sh``, the Windows installer's bundle — but an upgrade
that brings a newer Playwright did not: Chrome stores then failed with
"Executable doesn't exist". The upgrade can't fix that itself, because
the code running ``vibe-seller upgrade`` (or a ``git pull`` + restart)
is the OLD version, which can't know what the new Playwright needs. Only
the process running the new Playwright can, so:

* :func:`start_background_check` (server boot) asks the running
  Playwright what it needs and installs anything missing, in the
  background;
* :func:`ensure_ready_for_launch` (chrome backend, before launching)
  fails fast with a clear "still downloading" error instead of
  Playwright's, and retries a failed install — it never waits out a
  download, because launches run under the manager's global lock;
* ``vibe-seller install-browser`` runs the same install in the
  foreground, and ``vibe-seller upgrade`` calls it right after
  upgrading.

Downloads come from the faster of the official CDN and the npmmirror
mirror, by the same rules as ``install.sh``'s
``_install_playwright_chromium`` (which has to stay in bash: it runs
before this package is installed). A source qualifies only if every
file this Playwright version fetches is there, a mirror install that
fails is retried from the official CDN, and a user-set
``PLAYWRIGHT_DOWNLOAD_HOST`` is used as-is.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import http.client
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

from app.env_options import Options

logger = logging.getLogger(__name__)

MIRROR_HOST = 'https://cdn.npmmirror.com/binaries/playwright'
# '' = Playwright's own CDN, with its built-in fallbacks.
OFFICIAL_HOST = ''
CANDIDATE_HOSTS = (OFFICIAL_HOST, MIRROR_HOST)

_PROBE_BYTES = 4 * 1024 * 1024
_PROBE_TIMEOUT_S = 10.0
_DRY_RUN_TIMEOUT_S = 120.0
_CHECK_WAIT_S = 60.0
_MARKER = 'INSTALLATION_COMPLETE'

# A server launched by the Windows tray has no console; without this
# every `python -m playwright` child would flash one.
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class BrowserEngineNotReady(RuntimeError):
    """The Chromium this Playwright needs is missing or still downloading."""


@dataclass(frozen=True)
class Piece:
    """One browser download: where it installs and its primary URL."""

    location: Path
    url: str


def _playwright(*args: str) -> list[str]:
    return [sys.executable, '-m', 'playwright', *args]


_PROXY_VARS = (
    'https_proxy',
    'HTTPS_PROXY',
    'http_proxy',
    'HTTP_PROXY',
    'all_proxy',
    'ALL_PROXY',
)


def proxy_env() -> dict[str, str]:
    """Proxy vars to hand Playwright's downloader, beyond os.environ.

    Playwright's downloader reads proxy ENV VARS only, never the OS
    system proxy — which is all that Clash-style "system proxy" mode
    sets (Windows Internet Settings, macOS network preferences). With
    no proxy var exported, translate the system proxy into
    HTTPS_PROXY/HTTP_PROXY so the download takes the path the user's
    browser does; that ignored proxy is how the Windows installer's
    download hung for an hour. Proxy vars the user exported win.
    """
    if any(os.environ.get(v) for v in _PROXY_VARS):
        return {}
    system = urllib.request.getproxies()  # env empty → OS settings
    proxy = system.get('https') or system.get('http')
    if not proxy:
        return {}
    if '://' not in proxy:
        proxy = 'http://' + proxy
    return {'HTTPS_PROXY': proxy, 'HTTP_PROXY': proxy}


def _probe_opener() -> urllib.request.OpenerDirector:
    """urllib opener on the SAME network path the download will take.

    urllib on its own would fall back to the system proxy, while the
    downloader honours env vars only: a probe could then rate a CDN
    reachable and fast that the download can't reach.
    """
    extra = proxy_env()
    if extra:
        proxies = {'http': extra['HTTP_PROXY'], 'https': extra['HTTPS_PROXY']}
    else:
        proxies = urllib.request.getproxies_environment()
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))


def _env(host: str | None) -> dict[str, str]:
    """os.environ (+ system proxy), PLAYWRIGHT_DOWNLOAD_HOST unless None."""
    env = {**os.environ, **proxy_env()}
    if host is not None:
        # Playwright treats an empty value as unset (its own CDN).
        env['PLAYWRIGHT_DOWNLOAD_HOST'] = host
    return env


def user_download_host() -> str | None:
    """A PLAYWRIGHT_DOWNLOAD_HOST the user exported, or None."""
    return os.environ.get('PLAYWRIGHT_DOWNLOAD_HOST') or None


def parse_dry_run(output: str) -> list[Piece]:
    """Pieces from ``playwright install --dry-run`` output, one per dir."""
    pieces: dict[Path, Piece] = {}
    location: Path | None = None
    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith('Install location:'):
            location = Path(line.split(':', 1)[1].strip())
        elif line.startswith('Download url:') and location is not None:
            pieces.setdefault(
                location, Piece(location, line.split(':', 1)[1].strip())
            )
            location = None
    return list(pieces.values())


def dry_run(host: str | None) -> list[Piece]:
    """What ``playwright install chromium`` would fetch, via ``host``."""
    out = subprocess.run(
        _playwright('install', '--dry-run', 'chromium'),
        env=_env(host),
        capture_output=True,
        text=True,
        timeout=_DRY_RUN_TIMEOUT_S,
        check=True,
        creationflags=_NO_WINDOW,
    ).stdout
    return parse_dry_run(out)


def missing() -> list[Path]:
    """Install locations the running Playwright needs that are incomplete.

    Playwright itself treats a dir without its INSTALLATION_COMPLETE
    marker as not installed (a killed download leaves exactly that).
    """
    return [
        p.location
        for p in dry_run(None)
        if not (p.location / _MARKER).is_file()
    ]


def http_status(url: str) -> int:
    """HEAD status of ``url`` (redirects followed); 0 if unreachable."""
    req = urllib.request.Request(url, method='HEAD')
    try:
        with _probe_opener().open(req, timeout=_PROBE_TIMEOUT_S) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (OSError, ValueError, http.client.HTTPException):
        return 0


def download_speed(url: str) -> float:
    """Bytes/s fetching the first few MB of ``url``; 0.0 if it fails."""
    req = urllib.request.Request(
        url, headers={'Range': f'bytes=0-{_PROBE_BYTES - 1}'}
    )
    start = time.monotonic()
    got = 0
    try:
        with _probe_opener().open(req, timeout=_PROBE_TIMEOUT_S) as resp:
            if resp.status not in (200, 206):
                return 0.0
            while (
                got < _PROBE_BYTES
                and time.monotonic() - start < _PROBE_TIMEOUT_S
            ):
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                got += len(chunk)
    except (OSError, ValueError, http.client.HTTPException):
        pass  # count whatever arrived before the failure
    elapsed = time.monotonic() - start
    return got / elapsed if elapsed > 0 else 0.0


def pick_download_host(report: Callable[[str], None] = logger.info) -> str:
    """The faster qualifying source; OFFICIAL_HOST when none qualifies.

    A source qualifies only if EVERY file this Playwright version would
    fetch answers 200 there: the mirror's path layout has changed across
    Playwright versions, and a half-matching mirror would fail
    mid-install.
    """
    best, best_speed = OFFICIAL_HOST, -1.0
    for host in CANDIDATE_HOSTS:
        label = host or 'cdn.playwright.dev'
        try:
            urls = sorted({p.url for p in dry_run(host)})
        except (subprocess.SubprocessError, OSError) as e:
            report(f'{label}: could not list the downloads ({e})')
            continue
        if not urls:
            report(f'{label}: could not list the downloads')
            continue
        bad = next((u for u in urls if http_status(u) != 200), None)
        if bad is not None:
            report(f'{label}: unavailable ({bad.rsplit("/", 1)[-1]})')
            continue
        probe = next((u for u in urls if '/chrome-' in u), urls[0])
        speed = download_speed(probe)
        report(f'{label}: {speed / 1048576:.1f} MB/s')
        if speed > best_speed:
            best, best_speed = host, speed
    return best


def _say(msg: str) -> None:
    # Flushed: Playwright's own output interleaves with ours on the
    # same terminal, and a piped stdout would otherwise reorder them.
    print(msg, flush=True)  # noqa: T201 — `install-browser` CLI output


def install_foreground(report: Callable[[str], None] = _say) -> int:
    """Install what's missing, Playwright's progress on the terminal.

    For ``vibe-seller install-browser``. Returns 0 when nothing is
    missing afterwards.
    """
    need = missing()
    if not need:
        report('Chromium for this Playwright is already installed.')
        return 0
    report('Missing: ' + ', '.join(p.name for p in need))
    user_host = user_download_host()
    if user_host:
        report(f'Downloading from {user_host} (PLAYWRIGHT_DOWNLOAD_HOST)')
        hosts = [None]
    else:
        report('Checking which download source is faster...')
        host = pick_download_host(report)
        report(f'Downloading from {host or "cdn.playwright.dev"}')
        hosts = [host, OFFICIAL_HOST] if host else [OFFICIAL_HOST]
    for i, host in enumerate(hosts):
        if i:
            report('That failed — retrying from cdn.playwright.dev')
        rc = subprocess.run(
            _playwright('install', 'chromium'), env=_env(host)
        ).returncode
        if rc == 0:
            break
    still = missing()
    if still:
        report('Still missing: ' + ', '.join(p.name for p in still))
        return 1
    return 0


class Engine:
    """Single-flight check-and-install for one event loop.

    ``phase``: idle → checking → (ready | downloading → ready | failed).
    """

    def __init__(self) -> None:
        self.phase = 'idle'
        self.detail = ''
        self._task: asyncio.Task | None = None
        self._checked: asyncio.Event | None = None

    def start(self) -> asyncio.Task:
        """Start a check-and-install unless one is running or succeeded."""
        if self._task is None or self.phase == 'failed':
            self._checked = asyncio.Event()
            self.phase = 'checking'
            self._task = asyncio.create_task(self._run())
        return self._task

    async def _run(self) -> bool:
        assert self._checked is not None
        try:
            try:
                need = await asyncio.to_thread(missing)
            except (subprocess.SubprocessError, OSError) as e:
                # Fail open: a broken check must not block every launch;
                # Playwright reports a genuinely missing browser itself.
                logger.warning('Could not check the browser engine: %s', e)
                self.phase = 'ready'
                return True
            if not need:
                self.phase = 'ready'
                return True
            self.detail = ', '.join(p.name for p in need)
            self.phase = 'downloading'
        finally:
            self._checked.set()
        logger.info('Browser engine missing (%s) — installing', self.detail)
        ok = await self._install()
        self.phase = 'ready' if ok else 'failed'
        if ok:
            logger.info('Browser engine installed (%s)', self.detail)
        else:
            logger.error(
                'Browser engine install failed (%s); it is retried on the '
                'next Chrome launch, or run `vibe-seller install-browser`',
                self.detail,
            )
        return ok

    async def _install(self) -> bool:
        user_host = user_download_host()
        if user_host:
            hosts: list[str | None] = [None]
        else:
            host = await asyncio.to_thread(pick_download_host)
            hosts = [host, OFFICIAL_HOST] if host else [OFFICIAL_HOST]
        for host in hosts:
            logger.info(
                'Downloading Chromium from %s',
                user_host or host or 'cdn.playwright.dev',
            )
            rc, tail = await _run_install(host)
            if rc == 0:
                break
            logger.warning('playwright install exited %s: %s', rc, tail)
        try:
            return not await asyncio.to_thread(missing)
        except (subprocess.SubprocessError, OSError):
            return False

    async def ensure_ready_for_launch(self) -> None:
        """Raise BrowserEngineNotReady unless the engine is on disk.

        Waits only for the quick check, never for a download: launches
        run under the browser manager's global lock, and a minutes-long
        wait there would stall every other store's launch. A previous
        install that failed is retried here (``start`` restarts it).
        """
        self.start()
        assert self._checked is not None
        try:
            await asyncio.wait_for(self._checked.wait(), _CHECK_WAIT_S)
        except TimeoutError:
            return  # fail open, as in _run
        if self.phase == 'ready':
            return
        raise BrowserEngineNotReady(
            f'The Chrome browser engine ({self.detail}) is still being '
            f'downloaded for this Vibe Seller version — the first start '
            f'after an upgrade fetches ~250 MB. Try again in a few '
            f'minutes; progress is in the server log. To install it in '
            f'the foreground, run `vibe-seller install-browser`.'
        )

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


async def _run_install(host: str | None) -> tuple[int, str]:
    """``playwright install chromium`` via ``host``; (exit code, tail)."""
    proc = await asyncio.create_subprocess_exec(
        *_playwright('install', 'chromium'),
        env=_env(host),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=_NO_WINDOW,
    )
    try:
        out, _ = await proc.communicate()
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    tail = out.decode(errors='replace').strip().splitlines()[-3:]
    return proc.returncode or 0, ' | '.join(tail)


_engine = Engine()


def enabled() -> bool:
    return Options.ENSURE_BROWSER_ENGINE.get_bool()


def start_background_check() -> asyncio.Task | None:
    """Server boot: install the engine in the background if it's missing."""
    if not enabled():
        return None
    return _engine.start()


async def ensure_ready_for_launch() -> None:
    """Chrome backend: raise BrowserEngineNotReady if it can't launch yet."""
    if enabled():
        await _engine.ensure_ready_for_launch()


async def stop() -> None:
    await _engine.stop()
