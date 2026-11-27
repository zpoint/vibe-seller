"""`vibe-seller start --port N` tells the server it is on port N.

The server learns its own port from ``BACKEND_PORT`` and passes it to the
in-task MCP, the browser-use wrappers and the workspace assistant. The
CLI launcher used to leave it unset, so a server started on any port but
7777 sent every agent to 7777 (seen on native Windows: "All connection
attempts failed" on every in-task MCP tool). These pin that both launch
paths export the port they start the server on.
"""

import argparse
import os
from unittest import mock

import pytest

import app.cli as cli

pytestmark = pytest.mark.unit

PORT = 7897


def _args(**kw) -> argparse.Namespace:
    base = {
        'host': '127.0.0.1',
        'port': PORT,
        'foreground': False,
        'dev': False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    # setenv first so monkeypatch restores the original state (unset)
    # afterwards: the CLI writes os.environ directly.
    monkeypatch.setenv('BACKEND_PORT', 'placeholder')
    monkeypatch.delenv('BACKEND_PORT')
    monkeypatch.setenv('VIBE_HOME', str(tmp_path))
    (tmp_path / 'pids').mkdir()
    (tmp_path / 'logs').mkdir()


def test_the_daemon_is_started_with_its_port_in_backend_port(clean_env):
    proc = mock.Mock(pid=4242)
    proc.poll.return_value = None
    with (
        mock.patch.object(cli.subprocess, 'Popen', return_value=proc) as popen,
        mock.patch.object(cli.time, 'sleep'),
        mock.patch.object(cli, '_read_pid', return_value=None),
    ):
        cli._cmd_start(_args())
    env = popen.call_args.kwargs['env']
    assert env['BACKEND_PORT'] == str(PORT)


def test_the_foreground_server_sees_its_port_in_backend_port(clean_env):
    seen = {}

    def fake_run(*_a, **kw):
        seen['env'] = os.environ.get('BACKEND_PORT')
        seen['port'] = kw.get('port')

    fake_uvicorn = mock.Mock(run=fake_run)
    with mock.patch.dict('sys.modules', {'uvicorn': fake_uvicorn}):
        cli._cmd_start(_args(foreground=True))
    assert seen == {'env': str(PORT), 'port': PORT}
