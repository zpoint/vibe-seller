"""Never hand the agent a proxy that refuses connections.

Claude Code takes its HTTP proxy from ``https_proxy`` / ``HTTPS_PROXY``
/ ``http_proxy`` / ``HTTP_PROXY`` — from the env we spawn it with, and
again from the ``env`` block of ``~/.claude/settings.json``, which it
applies on top. It never reads the Windows system proxy. A machine set
up for Clash / v2rayN commonly carries ``HTTPS_PROXY=http://127.0.0.1:
<port>`` (a ``setx`` from an install guide, or a tool writing
settings.json) long after that proxy stopped running. The agent then
dies on its first model call with ``API Error: Connection refused — a
firewall or proxy may be blocking it (ECONNREFUSED)``, every task,
whatever provider is picked — and nothing in Vibe Seller set that
proxy, so nothing pointed at it (#168).

The invariant, enforced right before spawn: the agent is never started
with a proxy whose port refuses a TCP connection. A refused proxy fails
every request, so going direct can only help. A proxy that accepts, or
merely does not answer in time, is left alone — users who reach an
overseas endpoint through a working proxy keep it. The browser path
already strips proxies outright (``app/browser/proxy_env.py``); the
agent cannot, because some providers are only reachable through one.

Process-env proxies are dropped from the spawn env. A settings-file
proxy cannot be outranked from the env, so every proxy key is also
blanked through a ``--settings`` file, which Claude Code ranks above
its user, project and local settings. The user settings file is the
one Claude Code reads: ``$CLAUDE_CONFIG_DIR/settings.json`` when the
spawn env sets it, else ``~/.claude/settings.json``.
Neither touches the user's own files or env.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

from app.ai.claude_backend_utils import system_prompt_file
from app.ai.external_config import claude_settings_path

logger = logging.getLogger(__name__)

# Claude Code's own lookup order (``getProxyUrl``): first non-empty wins.
_PROXY_KEYS = ('https_proxy', 'HTTPS_PROXY', 'http_proxy', 'HTTP_PROXY')
_DEFAULT_PORTS = {'http': 80, 'https': 443}
# Long enough for Windows, which retries a SYN to a closed loopback port
# for ~2s before reporting it refused. A live proxy answers at once, so
# only a dead or unreachable one costs this wait.
_PROBE_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class AgentProxy:
    url: str
    key: str
    # 'environment', or the settings.json path that set it.
    source: str

    def display(self) -> str:
        """Scheme, host and port only: a proxy URL can carry a
        ``user:password@`` and query tokens, and the notice is logged
        and persisted to task history."""
        return redact_proxy_url(self.url)


def redact_proxy_url(url: str) -> str:
    parts = urlsplit(url if '://' in url else f'http://{url}')
    host = parts.hostname or '?'
    if ':' in host:
        host = f'[{host}]'
    try:
        port = parts.port
    except ValueError:
        port = None
    return f'{parts.scheme}://{host}' + (f':{port}' if port else '')


def _settings_env(env: dict[str, str]) -> dict[str, str]:
    path = claude_settings_path(env)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    env = data.get('env') if isinstance(data, dict) else None
    if not isinstance(env, dict):
        return {}
    return {k: v for k, v in env.items() if isinstance(v, str)}


def _bypasses_all(no_proxy: str) -> bool:
    return any(p.strip() == '*' for p in no_proxy.split(','))


def resolve_agent_proxy(env: dict[str, str]) -> AgentProxy | None:
    """The proxy Claude Code will use when spawned with *env*, or None."""
    settings_env = _settings_env(env)
    merged = {**env, **settings_env}
    no_proxy = merged.get('no_proxy') or merged.get('NO_PROXY') or ''
    if _bypasses_all(no_proxy):
        return None
    for key in _PROXY_KEYS:
        url = merged.get(key)
        if url:
            source = (
                str(claude_settings_path(env))
                if settings_env.get(key)
                else 'environment'
            )
            return AgentProxy(url=url, key=key, source=source)
    return None


async def proxy_refuses(url: str) -> bool:
    """True only when the proxy's port actively refuses a connection.

    A timeout or DNS failure is not proof the proxy is dead (a slow
    corporate proxy), so those count as usable.
    """
    parts = urlsplit(url if '://' in url else f'http://{url}')
    host = parts.hostname
    if not host:
        return False
    try:
        port = parts.port or _DEFAULT_PORTS.get(parts.scheme, 80)
    except ValueError:
        return False
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), _PROBE_TIMEOUT_S
        )
    except ConnectionRefusedError:
        return True
    except (TimeoutError, OSError):
        return False
    writer.close()
    return False


def dead_proxy_notice(proxy: AgentProxy) -> str:
    return (
        f'Proxy {proxy.display()} ({proxy.key}, from {proxy.source}) refuses '
        'connections, so the agent connects directly instead. Start '
        'that proxy, or remove the setting if you no longer use it.'
    )


def override_file(task_id: str, task_dir: Path | None) -> Path:
    """The ``--settings`` file, beside the session's system prompt."""
    return system_prompt_file(task_id, task_dir).with_suffix('.proxy.json')


async def drop_dead_proxy(
    cmd: list[str],
    env: dict[str, str],
    task_id: str,
    task_dir: Path | None,
    notify: Callable[[str, str], Awaitable[None]] | None = None,
) -> AgentProxy | None:
    """Strip a refusing proxy from the agent's spawn (*cmd* and *env*).

    Returns the proxy that was dropped, or None when there was none or
    it accepts connections. *notify* (role, text) tells the task.
    """
    proxy = resolve_agent_proxy(env)
    if proxy is None or not await proxy_refuses(proxy.url):
        return None
    # Go fully direct: drop every proxy key from the env, and blank them
    # in a --settings file that outranks every settings file claude
    # loads (user, project, local) — so no lower-ranked key, from any
    # file, can step in as the next dead proxy.
    for key in _PROXY_KEYS:
        env.pop(key, None)
    path = override_file(task_id, task_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({'env': dict.fromkeys(_PROXY_KEYS, '')}),
        encoding='utf-8',
    )
    cmd.extend(['--settings', str(path)])
    notice = dead_proxy_notice(proxy)
    logger.warning('%s', notice)
    if notify is not None:
        await notify('system', notice)
    return proxy
