"""``app.system_proxy``: decide the proxy a download takes, then hand it
over as env vars.

git, uv, pip, npm and Playwright read only ``*_proxy`` env vars, not
the OS system proxy the user's browser follows; and a stale exported
proxy kills every download. So the proxy is used only when it is alive
and reaches the download's targets; otherwise the download goes direct
with no proxy var at all.
"""

import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import socket
import threading

import pytest

from app import system_proxy as sp

pytestmark = pytest.mark.unit

_ALIVE = 'http://127.0.0.1:7890'


def _os_proxy(monkeypatch, proxies):
    monkeypatch.setattr(sp.urllib.request, 'getproxies', lambda: proxies)


def _decide(monkeypatch, reaches):
    """Pin the network answer: reaches(url, proxy) -> bool."""
    monkeypatch.setattr(sp, 'reaches', reaches)


def _proxy_vars(env):
    return {k: env[k] for k in sp.PROXY_VARS if k in env}


# -- translation: no checks ---------------------------------------------


def test_os_proxy_becomes_env_vars(monkeypatch):
    _os_proxy(monkeypatch, {'https': '127.0.0.1:7890'})
    assert sp.proxy_env() == {
        'HTTPS_PROXY': _ALIVE,
        'HTTP_PROXY': _ALIVE,
    }


def test_http_only_os_proxy_is_used(monkeypatch):
    _os_proxy(monkeypatch, {'http': 'http://10.0.0.2:3128'})
    assert sp.proxy_env()['HTTPS_PROXY'] == 'http://10.0.0.2:3128'


def test_exported_proxy_wins_over_the_os_proxy(monkeypatch):
    monkeypatch.setenv('https_proxy', 'http://mine:3128')
    _os_proxy(monkeypatch, {'https': 'sys:1'})
    assert sp.proxy_env() == {}
    assert sp.download_env()['https_proxy'] == 'http://mine:3128'
    assert 'HTTPS_PROXY' not in sp.download_env()


def test_no_proxy_anywhere_changes_nothing():
    assert sp.proxy_env() == {}
    assert sp.download_env() == dict(os.environ)


# -- the decision: proxy only when alive and reaching every target ------


def test_no_proxy_means_no_probe(monkeypatch):
    def boom(url, proxy):
        raise AssertionError('probed with nothing to probe')

    _decide(monkeypatch, boom)
    assert sp.checked_download_env((sp.GITHUB,)) == dict(os.environ)


def test_a_live_os_proxy_is_used(monkeypatch):
    _os_proxy(monkeypatch, {'https': '127.0.0.1:7890'})
    asked = []
    _decide(monkeypatch, lambda url, proxy: asked.append((url, proxy)) or True)
    env = sp.checked_download_env((sp.GITHUB, sp.PYPI))
    assert env['HTTPS_PROXY'] == _ALIVE
    # It was asked about every target, through that proxy.
    assert asked == [(sp.GITHUB, _ALIVE), (sp.PYPI, _ALIVE)]


def test_a_proxy_that_misses_a_target_is_dropped_entirely(monkeypatch):
    """Alive but not reaching PyPI: the download goes direct with no
    proxy var left to trip over, not half-proxied."""
    _os_proxy(monkeypatch, {'https': '127.0.0.1:7890'})
    _decide(monkeypatch, lambda url, proxy: url != sp.PYPI)
    env = sp.checked_download_env((sp.GITHUB, sp.PYPI))
    assert _proxy_vars(env) == {}


def test_a_dead_exported_proxy_is_stripped_from_every_spelling(monkeypatch):
    """The stale ``HTTPS_PROXY`` an old install guide left behind."""
    for var in ('https_proxy', 'HTTPS_PROXY', 'http_proxy', 'ALL_PROXY'):
        monkeypatch.setenv(var, 'http://127.0.0.1:1')
    monkeypatch.setenv('KEEP_ME', 'yes')
    _decide(monkeypatch, lambda url, proxy: False)
    env = sp.checked_download_env((sp.GITHUB,))
    assert _proxy_vars(env) == {}
    assert env['KEEP_ME'] == 'yes'


def test_a_live_exported_proxy_is_kept_as_set(monkeypatch):
    monkeypatch.setenv('https_proxy', 'http://mine:3128')
    _os_proxy(monkeypatch, {'https': 'sys:1'})
    _decide(monkeypatch, lambda url, proxy: proxy == 'http://mine:3128')
    env = sp.checked_download_env((sp.GITHUB,))
    assert env['https_proxy'] == 'http://mine:3128'
    assert 'HTTPS_PROXY' not in env


def test_a_socks_proxy_is_trusted_unprobed(monkeypatch):
    """urllib cannot speak SOCKS, so the answer would be a false no."""
    monkeypatch.setenv('all_proxy', 'socks5://127.0.0.1:7891')

    def boom(url, proxy):
        raise AssertionError('probed a socks proxy')

    _decide(monkeypatch, boom)
    assert sp.checked_download_env((sp.GITHUB,))['all_proxy'] == (
        'socks5://127.0.0.1:7891'
    )


def test_the_async_form_decides_the_same(monkeypatch):
    _os_proxy(monkeypatch, {'https': '127.0.0.1:7890'})
    _decide(monkeypatch, lambda url, proxy: True)
    env = asyncio.run(sp.checked_download_env_async((sp.GITHUB,)))
    assert env['HTTPS_PROXY'] == _ALIVE


# -- the probe itself, against real sockets -----------------------------


class _Origin(BaseHTTPRequestHandler):
    status = 200

    def do_HEAD(self):  # noqa: N802
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    """A local HTTP server; as an origin it answers HEAD, as a proxy it
    answers the absolute-URI HEAD a proxied http request becomes."""
    handler = type('H', (_Origin,), {})
    srv = HTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.handler = handler
    srv.url = f'http://127.0.0.1:{srv.server_port}'
    yield srv
    srv.shutdown()
    srv.server_close()


def test_probe_reaches_an_origin_directly(server):
    assert sp._probe(server.url + '/', None) is True


def test_probe_goes_through_a_live_proxy(server):
    # Not the origin's own address: only the proxy can answer this host.
    assert sp._probe('http://never-resolves.invalid/', server.url) is True


def test_probe_counts_any_http_answer_as_reached(server):
    server.handler.status = 404
    assert sp._probe('http://never-resolves.invalid/', server.url) is True


def test_probe_fails_when_the_proxy_refuses():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    assert sp._probe('http://x.invalid/', f'http://127.0.0.1:{port}') is False


def test_probe_fails_when_the_proxy_never_answers(monkeypatch):
    monkeypatch.setattr(sp, 'PROBE_TIMEOUT_S', 0.5)
    stall = socket.socket()
    stall.bind(('127.0.0.1', 0))
    stall.listen(1)  # accepts at the kernel, never replies
    try:
        port = stall.getsockname()[1]
        assert (
            sp._probe('http://x.invalid/', f'http://127.0.0.1:{port}') is False
        )
    finally:
        stall.close()


def test_probe_fails_for_an_unreachable_origin_direct():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    assert sp._probe(f'http://127.0.0.1:{port}/', None) is False


# -- the cache ----------------------------------------------------------


def test_an_answer_is_remembered_for_a_minute_then_asked_again(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sp, '_probe', lambda url, proxy: calls.append(url) or True
    )
    now = [1000.0]
    monkeypatch.setattr(sp.time, 'monotonic', lambda: now[0])
    assert sp.reaches('https://a.test', _ALIVE)
    assert sp.reaches('https://a.test', _ALIVE)
    assert calls == ['https://a.test']
    now[0] += sp._CACHE_TTL_S + 1
    assert sp.reaches('https://a.test', _ALIVE)
    assert calls == ['https://a.test'] * 2


def test_a_different_proxy_is_a_different_question(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sp, '_probe', lambda url, proxy: calls.append(proxy) or True
    )
    sp.reaches('https://a.test', 'http://p1:1')
    sp.reaches('https://a.test', 'http://p2:1')
    sp.reaches('https://a.test', None)
    assert calls == ['http://p1:1', 'http://p2:1', None]


# -- which site is probed -----------------------------------------------


def test_pypi_target_follows_the_configured_index():
    assert sp.pypi_target({}) == sp.PYPI
    assert sp.pypi_target({'PIP_INDEX_URL': 'https://m.test/simple'}) == (
        'https://m.test/simple'
    )
    assert sp.pypi_target({
        'UV_DEFAULT_INDEX': 'https://uv.test',
        'PIP_INDEX_URL': 'https://p',
    }) == ('https://uv.test')


def test_npm_target_follows_the_configured_registry():
    assert sp.npm_target({}) == sp.NPM
    assert sp.npm_target({'npm_config_registry': 'https://n.test/'}) == (
        'https://n.test/'
    )


def test_redaction_keeps_no_credentials():
    assert sp.redact_proxy_url('http://u:secret@10.0.0.1:3128/x?t=1') == (
        'http://10.0.0.1:3128'
    )
