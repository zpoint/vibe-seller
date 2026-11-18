"""Updates over a pretend network: every hop from "is there a release?"
to the bytes on disk takes a path that works.

The real code runs each step against local stand-ins for the internet
(``tests/fixtures/pretend_net.py``): one HTTPS origin answering as PyPI
(the JSON API and the simple index), as GitHub (the releases list, the
Windows release and its installer), and a CONNECT proxy in front of it
that counts the tunnels it carried. ``uv`` is a stand-in that really
fetches the index with ``curl``, which, like uv, reads only the
``*_proxy`` env vars.

Each step is driven under every network a user meets:

- no proxy anywhere;
- a live system proxy (the OS setting a CN user switches on), or a live
  exported one;
- a stale export left over while the system proxy works (found live);
- a stale export alone, and a system proxy that refuses the site — the
  direct network is the one that works.

What the user must get in each: the update is found and fetched, and
the bytes came through the proxy exactly when the proxy could carry
them — read off the proxy's own tunnel count, never off our env.
"""

import http.server
import json
import os
from pathlib import Path
import shutil
import urllib.request

import httpx
import pytest

from app import pip_update, system_proxy, uv_tool
import app.auto_update as au
from app.platform import IS_WINDOWS
import app.routers.system as system_router
import app.windows_update as wu
from tests.fixtures.pretend_net import (
    Proxy,
    dead_port,
    https_server,
    serve,
    stop,
)
from tests.fixtures.tls import self_signed

pytestmark = [
    pytest.mark.workflow,
    pytest.mark.skipif(IS_WINDOWS, reason='fake uv is a shell script'),
    pytest.mark.skipif(not shutil.which('curl'), reason='fake uv uses curl'),
]

CURRENT = '1.0.0'
LATEST = '9.9.9'
_INSTALLER = b'installer-bytes' * 1000

# uv's stand-in: fetch the index the way uv would (env proxies only),
# and log which proxy it was handed.
_UV = """#!/bin/sh
echo "${HTTPS_PROXY:-}" >> "$FAKE_LOG/uv-proxy"
curl -sSf --max-time 10 --cacert "$SSL_CERT_FILE" -o /dev/null \
  "$UV_DEFAULT_INDEX" || exit 1
echo "vibe-seller 9.9.9"
"""


def _documents(base: str) -> dict[str, tuple[str, bytes]]:
    release = {
        'tag_name': f'v{LATEST}',
        'name': f'v{LATEST}',
        'body': '## Highlights\n- faster\n',
        'html_url': f'{base}/releases/v{LATEST}',
        'published_at': '2026-11-01T00:00:00Z',
        'draft': False,
        'prerelease': False,
        'assets': [
            {
                'name': wu.ASSET_NAME,
                'browser_download_url': f'{base}/{wu.ASSET_NAME}',
                'size': len(_INSTALLER),
            }
        ],
    }
    pypi = {
        'info': {'version': LATEST},
        'releases': {
            CURRENT: [{'yanked': False}],
            LATEST: [{'yanked': False}],
        },
    }
    as_json = 'application/json'
    return {
        '/pypi/vibe-seller/json': (as_json, json.dumps(pypi).encode()),
        '/releases': (as_json, json.dumps([release]).encode()),
        '/releases/latest': (as_json, json.dumps(release).encode()),
        '/simple/': ('text/html', b'<html></html>'),
        f'/{wu.ASSET_NAME}': ('application/octet-stream', _INSTALLER),
    }


def _handler(documents, fetches):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_one_request(self):
            super().handle_one_request()
            if getattr(self, 'command', None) == 'GET':
                # (path, the peer's port): which connection it came on.
                fetches.append((self.path, self.client_address[1]))

        def do_HEAD(self):  # noqa: N802 — the probes
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_GET(self):  # noqa: N802
            kind, body = documents.get(
                self.path.partition('?')[0], ('text/plain', b'')
            )
            self.send_response(200 if body else 404)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


@pytest.fixture(scope='module')
def origin(tmp_path_factory):
    """PyPI + GitHub on one HTTPS origin.

    Yields (base_url, cert, port, fetches): every GET it served, with
    the port of the connection it came on.
    """
    base_dir = tmp_path_factory.mktemp('origin')
    cert, key = self_signed(base_dir)
    documents: dict = {}
    fetches: list[tuple[str, int]] = []
    server = https_server(_handler(documents, fetches), cert, key)
    base = f'https://127.0.0.1:{server.server_port}'
    documents.update(_documents(base))
    yield base, cert, server.server_port, fetches
    stop(server)


# shape -> whether the proxy is the path that works
SHAPES = {
    'no proxy': False,
    'live system proxy': True,
    'live exported proxy': True,
    'stale export, live system proxy': True,
    'stale export alone': False,
    'system proxy refuses the site': False,
}


@pytest.fixture(params=list(SHAPES))
def net(request, origin, monkeypatch, tmp_path):
    """The pretend internet, set to one network shape."""
    base, cert, port, fetches = origin
    fetches.clear()
    for var in (*system_proxy.PROXY_VARS, 'NO_PROXY', 'no_proxy'):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv('SSL_CERT_FILE', str(cert))
    monkeypatch.setenv(
        'VIBE_SELLER_PYPI_JSON_URL', f'{base}/pypi/vibe-seller/json'
    )
    monkeypatch.setenv('VIBE_SELLER_GITHUB_RELEASES_URL', f'{base}/releases')
    monkeypatch.setenv('VIBE_SELLER_RELEASES_URL', f'{base}/releases/latest')
    monkeypatch.setenv('UV_DEFAULT_INDEX', f'{base}/simple/')
    log = tmp_path / 'log'
    log.mkdir()
    monkeypatch.setenv('FAKE_LOG', str(log))
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    uv = bin_dir / 'uv'
    uv.write_text(_UV)
    uv.chmod(0o755)
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
    # The loopback origin must not be exempted by the OS's bypass list.
    monkeypatch.setattr(urllib.request, 'proxy_bypass', lambda host: False)
    # ``urlopen``'s process-wide opener keeps the proxies of its first
    # use; each shape here is a process started on that network.
    monkeypatch.setattr(urllib.request, '_opener', None)
    monkeypatch.setattr(system_proxy, 'PROBE_TIMEOUT_S', 1.0)
    system_proxy._reach_cache.clear()

    proxy = Proxy(port)
    serve(proxy)
    system: dict[str, str] = {}
    shape = request.param
    if shape == 'system proxy refuses the site':
        proxy.refuse = True
    if shape in (
        'live system proxy',
        'stale export, live system proxy',
        'system proxy refuses the site',
    ):
        system = {'https': proxy.url}
    if shape == 'live exported proxy':
        monkeypatch.setenv('HTTPS_PROXY', proxy.url)
    if shape.startswith('stale export'):
        monkeypatch.setenv('HTTPS_PROXY', dead_port())
    # The OS setting: what getproxies sees with the env empty, and what
    # the OS answers when asked directly.
    exported = os.environ.get('HTTPS_PROXY')

    def getproxies():
        return {'https': exported} if exported else dict(system)

    monkeypatch.setattr(urllib.request, 'getproxies', getproxies)
    # httpx holds its own reference to it.
    monkeypatch.setattr(httpx._utils, 'getproxies', getproxies)
    monkeypatch.setattr(system_proxy, '_platform_proxies', lambda: system)

    def uv_proxies() -> list[str]:
        return (log / 'uv-proxy').read_text().split('\n')[:-1]

    yield type(
        'Net',
        (),
        {
            'shape': shape,
            'via_proxy': SHAPES[shape],
            'proxy': proxy,
            'fetches': fetches,
            'uv_proxies': staticmethod(uv_proxies),
        },
    )()
    stop(proxy)


def _took_the_working_path(net, *paths: str) -> None:
    """Each of *paths* was fetched, every fetch through the proxy
    exactly when the proxy could carry it (read off the connection it
    arrived on, not off our env)."""
    fetched = {path.partition('?')[0] for path, _ in net.fetches}
    assert set(paths) <= fetched, (net.shape, fetched)
    carried = [
        (path, port in net.proxy.upstream_ports) for path, port in net.fetches
    ]
    assert all(via == net.via_proxy for _, via in carried), (
        net.shape,
        carried,
    )


def _uv_got_the_working_proxy(net) -> None:
    seen = net.uv_proxies()
    assert seen, 'uv never ran'
    assert set(seen) == {net.proxy.url if net.via_proxy else ''}, net.shape


@pytest.fixture
def _updates(tmp_path, monkeypatch):
    """A cold uv-tool install of CURRENT; update state under tmp."""
    au.reset_for_tests()
    updates = tmp_path / 'updates'
    monkeypatch.setattr(au.pip_update, 'updates_dir', lambda: updates)
    monkeypatch.setattr(
        au.update_idle, 'ready_file', lambda: updates / 'update-ready.json'
    )
    monkeypatch.setattr(wu, 'updates_root', lambda: updates)
    monkeypatch.setattr(au.update_events.telemetry, 'send', lambda *a, **k: 0)
    for module in (au, pip_update, wu):
        monkeypatch.setattr(module, 'get_version', lambda: CURRENT)
    # The prefetch asserts it did not move the install; nothing does.
    monkeypatch.setattr(
        pip_update.upgrade_restart, 'installed_version', lambda: CURRENT
    )
    yield updates
    au.reset_for_tests()


async def test_the_update_popup_finds_the_release(
    admin_client, net, monkeypatch
):
    monkeypatch.setattr(system_router, 'APP_VERSION', CURRENT)
    monkeypatch.setattr(system_router, '_update_check_cache', None)
    r = await admin_client.get('/api/system/update-check')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['update_available'] is True, (net.shape, body)
    assert body['latest_version'] == LATEST
    # The notes come from GitHub, a second host decided on its own.
    assert [rel['version'] for rel in body['releases']] == [LATEST]
    _took_the_working_path(net, '/pypi/vibe-seller/json', '/releases')


async def test_the_background_download_gets_ready(net, _updates):
    await au._cycle_unix()
    snap = au.snapshot()
    assert snap['state'] == au.READY, (net.shape, snap['error'])
    assert snap['version'] == LATEST
    # The check, then three uv fetches: the release, its browser, and
    # the running version kept for a rollback.
    assert len(net.uv_proxies()) == 3
    _uv_got_the_working_proxy(net)
    _took_the_working_path(net, '/pypi/vibe-seller/json', '/simple/')


def test_the_apply_installs_the_release(net, monkeypatch, tmp_path):
    """``vibe-seller upgrade --version`` — what the apply helper runs."""
    monkeypatch.setattr(uv_tool, 'receipt_path', lambda: tmp_path / 'r.toml')
    assert uv_tool.install_exactly(LATEST) == 0, net.shape
    _uv_got_the_working_proxy(net)
    _took_the_working_path(net, '/simple/')


def test_the_windows_installer_is_found_and_downloaded(net, _updates):
    offer = wu.check_for_update()
    assert offer is not None, net.shape
    assert offer['version'] == LATEST
    staged = wu.stage_installer(offer)
    assert Path(staged).read_bytes() == _INSTALLER
    _took_the_working_path(net, '/releases/latest', f'/{wu.ASSET_NAME}')
