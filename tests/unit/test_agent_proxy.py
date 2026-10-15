"""Unit tests for ``app.ai.agent_proxy``.

The invariant: the agent is never spawned with a proxy whose port
refuses connections (#168), from either source Claude Code reads — the
spawn env or ``~/.claude/settings.json`` ``env``. A proxy that accepts,
or that only times out, is left alone.
"""

import json
from pathlib import Path
import socket

import pytest

from app.ai import agent_proxy
from app.ai.agent_proxy import drop_dead_proxy, resolve_agent_proxy

pytestmark = pytest.mark.unit


@pytest.fixture
def settings(monkeypatch, tmp_path):
    path = tmp_path / 'claude' / 'settings.json'
    monkeypatch.setattr(agent_proxy, 'claude_settings_path', lambda: path)

    def write(env: dict) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'env': env}))
        return path

    return write


@pytest.fixture
def dead_url():
    """A loopback port that refuses: bound once, then closed."""
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return f'http://127.0.0.1:{port}'


@pytest.fixture
def live_url():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    s.listen()
    yield f'http://127.0.0.1:{s.getsockname()[1]}'
    s.close()


class Recorder:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, role: str, text: str) -> None:
        self.calls.append((role, text))


async def test_env_dead_proxy_is_dropped(settings, dead_url, tmp_path):
    env = {'HTTPS_PROXY': dead_url, 'http_proxy': dead_url, 'PATH': 'x'}
    cmd = ['claude']
    notify = Recorder()

    proxy = await drop_dead_proxy(cmd, env, 't1', tmp_path, notify)

    assert proxy is not None and proxy.source == 'environment'
    assert env == {'PATH': 'x'}
    assert cmd == ['claude']
    assert notify.calls and notify.calls[0][0] == 'system'
    assert dead_url in notify.calls[0][1]


async def test_settings_dead_proxy_is_blanked_by_flag_settings(
    settings, dead_url, tmp_path
):
    path = settings({'HTTPS_PROXY': dead_url})
    cmd = ['claude']

    proxy = await drop_dead_proxy(cmd, {}, 't1', tmp_path)

    assert proxy is not None and proxy.source == str(path)
    assert cmd[1] == '--settings'
    override = json.loads(Path(cmd[2]).read_text())
    assert override['env']['HTTPS_PROXY'] == ''
    assert override['env']['https_proxy'] == ''
    # The user's own settings.json is never edited.
    assert json.loads(path.read_text()) == {'env': {'HTTPS_PROXY': dead_url}}


async def test_settings_outranks_env_like_claude_code(
    settings, dead_url, live_url, tmp_path
):
    settings({'HTTPS_PROXY': dead_url})
    env = {'HTTPS_PROXY': live_url}
    cmd = ['claude']

    assert await drop_dead_proxy(cmd, env, 't1', tmp_path) is not None
    assert 'HTTPS_PROXY' not in env and '--settings' in cmd


async def test_dead_primary_drops_every_source(settings, dead_url, tmp_path):
    # Lower-ranked settings HTTP_PROXY must not step in once the env
    # HTTPS_PROXY is dropped.
    settings({'HTTP_PROXY': dead_url})
    env = {'HTTPS_PROXY': dead_url}
    cmd = ['claude']

    await drop_dead_proxy(cmd, env, 't1', tmp_path)

    assert env == {}
    assert '--settings' in cmd


async def test_live_proxy_is_kept(settings, live_url, tmp_path):
    env = {'HTTPS_PROXY': live_url}
    cmd = ['claude']

    assert await drop_dead_proxy(cmd, env, 't1', tmp_path) is None
    assert env == {'HTTPS_PROXY': live_url} and cmd == ['claude']


async def test_timeout_is_not_proof_of_death(monkeypatch, settings, tmp_path):
    async def hang(*_a, **_k):
        raise TimeoutError

    monkeypatch.setattr(agent_proxy.asyncio, 'open_connection', hang)
    env = {'HTTPS_PROXY': 'http://proxy.corp.example:3128'}

    assert await drop_dead_proxy(['claude'], env, 't1', tmp_path) is None
    assert 'HTTPS_PROXY' in env


async def test_no_proxy_star_means_no_proxy(settings, dead_url):
    assert (
        resolve_agent_proxy({'HTTPS_PROXY': dead_url, 'NO_PROXY': '*'}) is None
    )


def test_lowercase_wins_like_claude_code(settings):
    proxy = resolve_agent_proxy({
        'HTTPS_PROXY': 'http://upper:1',
        'https_proxy': 'http://lower:1',
    })
    assert proxy is not None and proxy.url == 'http://lower:1'
