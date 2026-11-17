"""Settings → Dida365 → install, over a pretend network.

The user's click goes HTTP → route → installer → a REAL ``git clone`` and
the proxy probes, against local stand-ins for the internet:

- an HTTPS "GitHub" (a bare repo served by ``git http-backend``) that
  also answers as the package index;
- a CONNECT proxy that counts the tunnels git and the probes open;
- the failure shapes a proxy user meets: a proxy that refuses the
  target, one that never answers (black hole), one that is gone.

What the user must see in each: the install finishes, and the download
took the path the probe chose — through the proxy only when the proxy
could serve it. ``checked_download_env`` is never called directly here;
whether the proxy reached ``git`` is read off the proxy's own tunnel
count, not off our env.
"""

import asyncio
import contextlib
import http.server
import os
from pathlib import Path
import select
import shutil
import socket
import socketserver
import ssl
import subprocess
import threading
import urllib.request

import pytest

from app import system_proxy, ticktick_mcp_install as inst
from app.platform import IS_WINDOWS
from tests.fixtures.tls import self_signed

pytestmark = [
    pytest.mark.workflow,
    pytest.mark.skipif(IS_WINDOWS, reason='fake uv is a shell script'),
    pytest.mark.skipif(not shutil.which('git'), reason='needs a real git'),
]

_UV = """#!/bin/sh
echo "${HTTPS_PROXY:-}" > "$FAKE_LOG/uv-proxy"
if [ "$1" = venv ]; then
  mkdir -p "$2/bin"
  touch "$2/bin/python3"
fi
exit 0
"""


class _TLSServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    context: ssl.SSLContext

    def get_request(self):
        sock, addr = self.socket.accept()
        # Handshake in the handler's thread, not in the accept loop.
        return (
            self.context.wrap_socket(
                sock, server_side=True, do_handshake_on_connect=False
            ),
            addr,
        )

    def handle_error(self, request, client_address):
        pass  # a probe that hangs up mid-handshake is not a test failure


class _GitHub(http.server.BaseHTTPRequestHandler):
    """``git http-backend`` over HTTPS; any HEAD is a 200 (the probes)."""

    root: Path

    def log_message(self, *args):
        pass

    def do_HEAD(self):  # noqa: N802
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _backend(self):
        path, _, query = self.path.partition('?')
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        env = {
            'PATH': os.environ['PATH'],
            'HOME': str(self.root),
            'GIT_PROJECT_ROOT': str(self.root),
            'GIT_HTTP_EXPORT_ALL': '1',
            'REQUEST_METHOD': self.command,
            'PATH_INFO': path,
            'QUERY_STRING': query,
            'CONTENT_TYPE': self.headers.get('Content-Type', ''),
            'CONTENT_LENGTH': str(len(body)),
            'REMOTE_ADDR': '127.0.0.1',
        }
        if self.headers.get('Content-Encoding'):
            env['HTTP_CONTENT_ENCODING'] = self.headers['Content-Encoding']
        out = subprocess.run(
            ['git', 'http-backend'], input=body, env=env, capture_output=True
        ).stdout
        head, _, payload = out.partition(b'\r\n\r\n')
        status, headers = 200, []
        for line in head.split(b'\r\n'):
            key, _, value = line.partition(b': ')
            if key.lower() == b'status':
                status = int(value.split()[0])
            elif key:
                headers.append((key.decode(), value.decode()))
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = _backend  # noqa: N815


class _Proxy(socketserver.ThreadingTCPServer):
    """A CONNECT proxy that tunnels everything to the one local origin."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, origin_port: int):
        super().__init__(('127.0.0.1', 0), _Tunnel)
        self.origin_port = origin_port
        self.refuse = False
        self.asked = 0  # CONNECTs received
        self.tunnels = 0  # CONNECTs that got through

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self.server_address[1]}'


class _Tunnel(socketserver.StreamRequestHandler):
    def handle(self):
        request = self.rfile.readline().split()
        while self.rfile.readline().strip():
            pass
        if len(request) < 2 or request[0] != b'CONNECT':
            self.wfile.write(b'HTTP/1.1 405 Method Not Allowed\r\n\r\n')
            return
        proxy: _Proxy = self.server  # type: ignore[assignment]
        proxy.asked += 1
        if proxy.refuse:
            self.wfile.write(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
            return
        upstream = socket.create_connection(('127.0.0.1', proxy.origin_port))
        proxy.tunnels += 1
        self.wfile.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
        self.wfile.flush()
        with contextlib.closing(upstream):
            sockets = [self.connection, upstream]
            while True:
                ready, _, _ = select.select(sockets, [], [], 30)
                if not ready:
                    return
                for sock in ready:
                    data = sock.recv(65536)
                    if not data:
                        return
                    (
                        upstream if sock is self.connection else self.connection
                    ).sendall(data)


def _serve(server) -> None:
    threading.Thread(target=server.serve_forever, daemon=True).start()


@pytest.fixture(scope='module')
def github(tmp_path_factory):
    """A real bare repo behind HTTPS; yields (base_url, cert_path)."""
    base = tmp_path_factory.mktemp('github')
    git = ['git', '-c', 'user.email=t@example.com', '-c', 'user.name=t']
    work = base / 'work'
    (work / 'ticktick_mcp').mkdir(parents=True)
    (work / 'ticktick_mcp' / '__init__.py').write_text('')
    isolated = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull}
    for cmd in (
        ['init', '-q', '-b', 'main'],
        ['add', '.'],
        ['commit', '-q', '-m', 'x', '--no-gpg-sign'],
    ):
        subprocess.run(git + cmd, cwd=work, check=True, env=isolated)
    root = base / 'served'
    root.mkdir()
    subprocess.run(
        [
            'git',
            'clone',
            '-q',
            '--bare',
            str(work),
            str(root / 'ticktick-mcp.git'),
        ],
        check=True,
        env=isolated,
    )
    cert, key = self_signed(base)
    handler = type('Handler', (_GitHub,), {'root': root})
    server = _TLSServer(('127.0.0.1', 0), handler)
    server.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.context.load_cert_chain(cert, key)
    _serve(server)
    yield f'https://127.0.0.1:{server.server_port}', cert, server.server_port
    server.shutdown()
    server.server_close()


@pytest.fixture
def net(github, monkeypatch, tmp_path):
    """The pretend internet: no ambient proxy, GitHub + index = *github*."""
    url, cert, port = github
    for var in (*system_proxy.PROXY_VARS, 'NO_PROXY', 'no_proxy'):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv('SSL_CERT_FILE', str(cert))
    monkeypatch.setenv('GIT_SSL_CAINFO', str(cert))
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('UV_DEFAULT_INDEX', f'{url}/simple/')
    log = tmp_path / 'log'
    log.mkdir()
    monkeypatch.setenv('FAKE_LOG', str(log))
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    uv = bin_dir / 'uv'
    uv.write_text(_UV)
    uv.chmod(0o755)
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
    monkeypatch.setattr(inst, 'TICKTICK_MCP_REPO', f'{url}/ticktick-mcp.git')
    monkeypatch.setattr(inst, 'GITHUB', url)
    monkeypatch.setattr(inst, 'TICKTICK_MCP_DIR', tmp_path / 'ticktick-mcp')
    # The loopback origin must not be exempted by the OS's bypass list.
    monkeypatch.setattr(urllib.request, 'proxy_bypass', lambda host: False)
    monkeypatch.setattr(urllib.request, 'getproxies', dict)
    monkeypatch.setattr(system_proxy, 'PROBE_TIMEOUT_S', 1.0)
    monkeypatch.setattr(inst, '_ONE_AT_A_TIME', asyncio.Lock())
    system_proxy._reach_cache.clear()
    proxy = _Proxy(port)
    _serve(proxy)
    yield type(
        'Net',
        (),
        {
            'proxy': proxy,
            'dir': tmp_path / 'ticktick-mcp',
            'uv_proxy': lambda self: (log / 'uv-proxy').read_text().strip(),
            'use_system_proxy': lambda self, url: monkeypatch.setattr(
                urllib.request, 'getproxies', lambda: {'https': url}
            ),
            'export_proxy': lambda self, url: monkeypatch.setenv(
                'HTTPS_PROXY', url
            ),
        },
    )()
    proxy.shutdown()
    proxy.server_close()


def _dead_port() -> str:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return f'http://127.0.0.1:{sock.getsockname()[1]}'


@pytest.fixture
def black_hole():
    """Accepts connections, never answers one."""
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(8)
    yield f'http://127.0.0.1:{sock.getsockname()[1]}'
    sock.close()


async def _click(client):
    return await client.post('/api/dida365/setup-mcp')


def _installed(net, response) -> None:
    assert response.status_code == 200, response.text
    assert response.json()['already_installed'] is False
    assert (net.dir / inst.INSTALL_MARKER).is_file()
    assert (net.dir / 'ticktick_mcp' / '__init__.py').is_file()


async def test_no_proxy_installs_directly(admin_client, net):
    _installed(net, await _click(admin_client))
    assert net.proxy.asked == 0
    assert net.uv_proxy() == ''


async def test_a_live_system_proxy_carries_the_download(admin_client, net):
    net.use_system_proxy(net.proxy.url)
    _installed(net, await _click(admin_client))
    # The probes AND git's own clone each opened a tunnel.
    assert net.proxy.tunnels >= 3
    assert net.uv_proxy() == net.proxy.url


async def test_a_live_exported_proxy_is_kept(admin_client, net):
    net.export_proxy(net.proxy.url)
    _installed(net, await _click(admin_client))
    assert net.proxy.tunnels >= 3
    assert net.uv_proxy() == net.proxy.url


async def test_a_mirror_with_credentials_keeps_a_working_proxy(
    admin_client, net, github, monkeypatch
):
    """``user:token@`` in the index URL (a private mirror) is not the
    proxy's fault: the probe asks the host, and the proxy stays."""
    url = github[0].replace('https://', 'https://me:t0ken@')
    monkeypatch.setenv('UV_DEFAULT_INDEX', f'{url}/simple/')
    net.use_system_proxy(net.proxy.url)
    _installed(net, await _click(admin_client))
    assert net.proxy.tunnels >= 3
    assert net.uv_proxy() == net.proxy.url


async def test_a_proxy_that_refuses_the_target_is_not_used(admin_client, net):
    net.proxy.refuse = True
    net.use_system_proxy(net.proxy.url)
    _installed(net, await _click(admin_client))
    assert net.proxy.asked >= 1  # it was asked, and said no
    assert net.proxy.tunnels == 0  # so git never went through it
    assert net.uv_proxy() == ''


async def test_a_dead_exported_proxy_is_dropped(admin_client, net):
    net.export_proxy(_dead_port())
    _installed(net, await _click(admin_client))
    assert net.proxy.tunnels == 0
    assert net.uv_proxy() == ''


async def test_a_black_hole_proxy_is_dropped_after_the_probe_deadline(
    admin_client, net, black_hole
):
    net.use_system_proxy(black_hole)
    _installed(net, await _click(admin_client))
    assert net.proxy.tunnels == 0
    assert net.uv_proxy() == ''


async def test_nothing_reachable_says_why_and_leaves_nothing(
    admin_client, net, monkeypatch
):
    monkeypatch.setattr(inst, 'TICKTICK_MCP_REPO', f'{_dead_port()}/x.git')
    net.use_system_proxy(_dead_port())
    failed = await _click(admin_client)
    assert failed.status_code == 500
    assert 'ticktick-mcp' in failed.json()['detail']
    assert not net.dir.exists()
