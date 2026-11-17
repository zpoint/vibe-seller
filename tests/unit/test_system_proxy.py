"""``app.system_proxy``: the OS proxy, as env vars, for downloads.

git, uv, npm and Playwright read only ``*_proxy`` env vars, not the OS
system proxy the user's browser follows.
"""

import os

import pytest

from app import system_proxy

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_proxy_env(monkeypatch):
    for var in system_proxy.PROXY_VARS:
        monkeypatch.delenv(var, raising=False)


def _os_proxy(monkeypatch, proxies):
    monkeypatch.setattr(
        system_proxy.urllib.request, 'getproxies', lambda: proxies
    )


def test_os_proxy_becomes_env_vars(monkeypatch):
    _os_proxy(monkeypatch, {'https': '127.0.0.1:7890'})
    assert system_proxy.proxy_env() == {
        'HTTPS_PROXY': 'http://127.0.0.1:7890',
        'HTTP_PROXY': 'http://127.0.0.1:7890',
    }


def test_http_only_os_proxy_is_used(monkeypatch):
    _os_proxy(monkeypatch, {'http': 'http://10.0.0.2:3128'})
    assert system_proxy.proxy_env()['HTTPS_PROXY'] == 'http://10.0.0.2:3128'


def test_exported_proxy_wins_over_the_os_proxy(monkeypatch):
    monkeypatch.setenv('https_proxy', 'http://mine:3128')
    _os_proxy(monkeypatch, {'https': 'sys:1'})
    assert system_proxy.proxy_env() == {}
    assert system_proxy.download_env()['https_proxy'] == 'http://mine:3128'
    assert 'HTTPS_PROXY' not in system_proxy.download_env()


def test_no_proxy_anywhere_changes_nothing(monkeypatch):
    _os_proxy(monkeypatch, {})
    assert system_proxy.proxy_env() == {}
    assert system_proxy.download_env() == dict(os.environ)


def test_download_env_carries_the_rest_of_the_environment(monkeypatch):
    monkeypatch.setenv('SOME_OTHER_VAR', 'kept')
    _os_proxy(monkeypatch, {'https': '127.0.0.1:7890'})
    env = system_proxy.download_env()
    assert env['SOME_OTHER_VAR'] == 'kept'
    assert env['HTTPS_PROXY'] == 'http://127.0.0.1:7890'
