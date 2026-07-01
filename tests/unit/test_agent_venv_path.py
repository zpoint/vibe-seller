"""Unit test: the task agent's python/pip must be the shared agent venv.

Guards against the infra bug where ``claude_backend`` pointed the agent's
``PATH`` / ``VIRTUAL_ENV`` at the server/install venv. On a packaged
install that venv is built by ``uv pip install`` with no pip seeded, so
an agent ``pip install X`` fails and the agent falls back to a stray
system Python — installing into a different interpreter than it runs
(the Windows symptom: landing on ``...\\Programs\\Python\\Python313``).
The fix points python/pip at the shared, pip-bootstrapped, reused agent
venv ``~/.vibe-seller/.venv`` (mirroring the workspace assistant).
"""

import os
from pathlib import Path

import pytest

from app.ai.claude_backend import apply_agent_venv_path
from app.platform import venv_bin_dir

pytestmark = pytest.mark.unit


def _mkvenv(root: Path) -> Path:
    """Create a fake venv (its bin/Scripts dir) and return the bin dir."""
    bin_dir = venv_bin_dir(root)
    bin_dir.mkdir(parents=True, exist_ok=True)
    return bin_dir


class TestAgentVenvPath:
    def test_python_pip_resolve_to_shared_venv_not_server(self, tmp_path):
        """python/pip resolve to ~/.vibe-seller/.venv, ahead of the
        server venv, and VIRTUAL_ENV points at the shared venv."""
        vibe_home = tmp_path / 'vibe'
        shared_bin = _mkvenv(vibe_home / '.venv')
        server_bin = _mkvenv(tmp_path / 'server' / '.venv')

        env = {'PATH': '/usr/bin'}
        apply_agent_venv_path(
            env, store_slug=None, vibe_home=vibe_home, server_bin=server_bin
        )

        assert env['VIRTUAL_ENV'] == str(vibe_home / '.venv')
        parts = env['PATH'].split(os.pathsep)
        assert str(shared_bin) in parts, 'shared venv bin missing from PATH'
        assert str(server_bin) in parts
        # Shared venv must win python/pip resolution over the server venv.
        assert parts.index(str(shared_bin)) < parts.index(str(server_bin))

    def test_store_wrapper_sits_ahead_of_both_venvs(self, tmp_path):
        """The per-store browser-use wrapper must be first on PATH so
        every `browser-use` call goes through session/CDP injection."""
        vibe_home = tmp_path / 'vibe'
        shared_bin = _mkvenv(vibe_home / '.venv')
        server_bin = _mkvenv(tmp_path / 'server' / '.venv')
        store_bin = vibe_home / 'bin' / 'mystore'
        store_bin.mkdir(parents=True)

        env = {'PATH': '/usr/bin'}
        apply_agent_venv_path(
            env,
            store_slug='mystore',
            vibe_home=vibe_home,
            server_bin=server_bin,
        )

        parts = env['PATH'].split(os.pathsep)
        assert (
            parts.index(str(store_bin))
            < parts.index(str(shared_bin))
            < parts.index(str(server_bin))
        )

    def test_regression_agent_not_pinned_to_server_venv(self, tmp_path):
        """The exact bug: the active env must NOT be the server venv.

        Reverting claude_backend to prepend only the server venv
        (``sys.executable``) and set ``VIRTUAL_ENV`` to it would make
        this fail.
        """
        vibe_home = tmp_path / 'vibe'
        _mkvenv(vibe_home / '.venv')
        server_venv = tmp_path / 'server' / '.venv'
        server_bin = _mkvenv(server_venv)

        env = {'PATH': '/usr/bin'}
        apply_agent_venv_path(
            env, store_slug=None, vibe_home=vibe_home, server_bin=server_bin
        )
        assert env['VIRTUAL_ENV'] != str(server_venv)
