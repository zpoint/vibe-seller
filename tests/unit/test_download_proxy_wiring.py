"""Every download the server or CLI starts takes the checked proxy.

``app.system_proxy`` decides (tested there); these pin that each place
that spawns a download actually hands it that env, and asks about the
site that download visits — not just that the helper exists. Each is
run twice: a proxy that reaches the target (the child gets it) and one
that does not (the child gets no proxy var at all).
"""

import asyncio
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from app import claude_cli_install, cli, system_proxy
from app.platform import venv_bin_dir, venv_python
from app.workspace import skills_sync, venv_bootstrap

pytestmark = pytest.mark.unit

_PROXY = 'http://127.0.0.1:7890'


class _Asked(list):
    """The (url, proxy) pairs probed, and whether the answer was yes."""

    proxied = False


@pytest.fixture(params=['reaches', 'blocked'])
def network(request, monkeypatch):
    """A system proxy whose answer to every probe is fixed; returns the
    list of (url, proxy) asked."""
    monkeypatch.setattr(
        system_proxy.urllib.request,
        'getproxies',
        lambda: {'https': '127.0.0.1:7890'},
    )
    asked = _Asked()
    ok = request.param == 'reaches'

    def reaches(url, proxy):
        asked.append((url, proxy))
        return ok

    monkeypatch.setattr(system_proxy, 'reaches', reaches)
    asked.proxied = ok
    return asked


def _proxy_of(env):
    return env.get('HTTPS_PROXY')


def _assert_env(env, network):
    assert _proxy_of(env) == (_PROXY if network.proxied else None)
    assert 'HTTP_PROXY' in env or not network.proxied


class _Done:
    returncode = 0

    async def communicate(self):
        return b'', b''


def _capture_async(monkeypatch):
    seen = {}

    async def fake_exec(*cmd, **kw):
        seen['cmd'] = cmd
        seen['env'] = kw.get('env')
        return _Done()

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_exec)
    return seen


def _capture_run(monkeypatch, module):
    seen = {}

    def fake_run(cmd, *a, **kw):
        seen['cmd'] = cmd
        seen['env'] = kw.get('env')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(module.subprocess, 'run', fake_run)
    return seen


async def test_venv_tool_bootstrap_asks_about_the_index(network, monkeypatch):
    seen = _capture_async(monkeypatch)
    await venv_bootstrap.bootstrap_venv_tools(Path('/venv'))
    _assert_env(seen['env'], network)
    assert [u for u, _ in network] == [system_proxy.PYPI]


async def test_venv_tool_bootstrap_probes_a_configured_mirror(
    network, monkeypatch
):
    monkeypatch.setenv('PIP_INDEX_URL', 'https://mirror.test/simple')
    _capture_async(monkeypatch)
    await venv_bootstrap.bootstrap_venv_tools(Path('/venv'))
    assert [u for u, _ in network] == ['https://mirror.test/simple']


async def test_skill_requirements_install(network, monkeypatch, tmp_path):
    mgr = skills_sync.SkillsSyncManager()
    mgr._dest_dir = tmp_path / '.claude' / 'skills'
    skill = mgr._dest_dir / 'a-skill'
    skill.mkdir(parents=True)
    (skill / 'requirements.txt').write_text('reportlab==4.2.5\n')
    venv = tmp_path / '.venv'
    venv_bin_dir(venv).mkdir(parents=True)
    venv_python(venv).write_text('fake')
    (mgr._dest_dir / '.sync_meta.json').write_text(json.dumps({}))
    seen = _capture_async(monkeypatch)
    with patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path):
        await mgr._install_skill_deps()
    assert 'pip' in seen['cmd'], 'the install did not run'
    _assert_env(seen['env'], network)
    assert [u for u, _ in network] == [system_proxy.PYPI]


def test_claude_cli_install_step(network, monkeypatch, tmp_path):
    package = tmp_path.joinpath('node_modules', *claude_cli_install.PACKAGE)
    package.mkdir(parents=True)
    (package / 'install.cjs').write_text('//\n')
    monkeypatch.setattr(claude_cli_install, 'runs', lambda cli_path: False)
    monkeypatch.setattr(
        claude_cli_install.shutil, 'which', lambda name: '/usr/bin/node'
    )
    seen = _capture_run(monkeypatch, claude_cli_install)
    claude_cli_install.finish(tmp_path)
    assert seen['cmd'][-1] == 'install.cjs'
    _assert_env(seen['env'], network)
    assert [u for u, _ in network] == [system_proxy.NPM]


def test_upgrade_of_the_claude_cli_through_npm(network, monkeypatch):
    monkeypatch.setattr(
        cli, '_claude_cli_upgrade_cmd', lambda: ['npm', 'install', 'x']
    )
    monkeypatch.setattr(claude_cli_install, 'finish', lambda home: 0)
    seen = _capture_run(monkeypatch, cli)
    assert cli._upgrade_claude_cli() == 0
    assert seen['cmd'][0] == 'npm'
    _assert_env(seen['env'], network)
    assert [u for u, _ in network] == [system_proxy.NPM]
