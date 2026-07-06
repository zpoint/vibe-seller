"""Proxy-free environment for spawning browser subprocesses.

A browser client must never inherit our server's system-proxy env (e.g. a
user's Clash on 127.0.0.1:7890): Ziniao reaches its own domestic servers
directly and drives per-store residential proxies itself, so a leaked
system proxy can reset the OS proxy config and break browser launches.
Chrome/other backends manage their own proxying too. See
docs/ziniao-concurrency.md.

NOTE: on macOS the Ziniao client is launched via ``open -a`` (launchd),
which ignores a subprocess env — so this only takes effect on the
Windows/Linux direct-exec launch paths.
"""

import os

_PROXY_ENV_VARS = frozenset({
    'http_proxy',
    'https_proxy',
    'all_proxy',
    'ftp_proxy',
    'HTTP_PROXY',
    'HTTPS_PROXY',
    'ALL_PROXY',
    'FTP_PROXY',
})


def browser_launch_env() -> dict[str, str]:
    """Process env with system-proxy vars stripped, for spawning browsers."""
    env = {k: v for k, v in os.environ.items() if k not in _PROXY_ENV_VARS}
    env['NO_PROXY'] = '*'
    env['no_proxy'] = '*'
    return env
