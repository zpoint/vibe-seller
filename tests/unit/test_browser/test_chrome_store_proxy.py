"""How a store's proxy setting becomes Playwright's ``proxy`` option.

The behaviour on a real browser (traffic, bypass, auth challenge) is
pinned in tests/integration/test_chrome_store_launch.py; this covers the
spellings people type into the store form.
"""

import pytest

from app.browser.chrome import store_proxy_option


@pytest.mark.unit
@pytest.mark.parametrize(
    'config, expected',
    [
        ({}, None),
        ({'proxy_server': '   '}, None),
        (
            {'proxy_server': 'http://127.0.0.1:7890'},
            {'server': 'http://127.0.0.1:7890'},
        ),
        (
            {'proxy_server': ' socks5://127.0.0.1:7891 '},
            {'server': 'socks5://127.0.0.1:7891'},
        ),
        (
            {'proxy_server': '127.0.0.1:7890'},
            {'server': '127.0.0.1:7890'},
        ),
        (
            {
                'proxy_server': 'http://vs-user:p%40ss@proxy.example:8080',
                'proxy_bypass': '.amazon.com,.google.com',
            },
            {
                'server': 'http://proxy.example:8080',
                'username': 'vs-user',
                'password': 'p@ss',
                'bypass': '.amazon.com,.google.com',
            },
        ),
        (
            {'proxy_server': 'vs-user:secret@proxy.example:8080'},
            {
                'server': 'proxy.example:8080',
                'username': 'vs-user',
                'password': 'secret',
            },
        ),
    ],
)
def test_store_proxy(config, expected):
    assert store_proxy_option(config) == expected
