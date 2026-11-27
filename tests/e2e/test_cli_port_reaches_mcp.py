"""E2E: a server started by `vibe-seller start --port N` hands its agents N.

Seen on native Windows: a server started as `vibe-seller start --port
7797` wrote `--port 7777` into every task's `.mcp.json`, so every in-task
MCP tool failed with "All connection attempts failed". This test starts a
real server through the CLI on a free non-default port, runs a task with
the mock agent, then takes the vibe-seller MCP entry the server wrote for
that task and drives it like Claude Code would: `initialize`, then a
`tools/call` that has to reach the server's API.

Self-contained: it starts and stops its own server (own VIBE_HOME, the
outside-call switches CI uses), so it does not need E2E_BASE_URL.
"""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest

pytestmark = [pytest.mark.e2e]

REPO = Path(__file__).resolve().parents[2]
MOCK_CLI = REPO / 'tests' / 'e2e' / 'mock_cli.py'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _cli(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, '-m', 'app.cli', *args],
        env=env,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
def cli_server(tmp_path):
    port = _free_port()
    home = tmp_path / 'home'
    env = {k: v for k, v in os.environ.items() if k != 'BACKEND_PORT'}
    env.update(
        VIBE_HOME=str(home),
        VIBE_SELLER_TELEMETRY='0',
        VIBE_EXTERNAL_SYNC='off',
        VIBE_GITHUB_MAIN_SYNC='off',
        VIBE_ADS_SERVICE_URL='http://127.0.0.1:7791',
        VIBE_SKIP_FRONTEND_BUILD='1',
        MOCK_CLI=str(MOCK_CLI),
        PYTHONPATH=str(REPO),
    )
    started = _cli(env, 'start', '--port', str(port))
    assert started.returncode == 0, started.stdout + started.stderr
    base = f'http://127.0.0.1:{port}'
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if httpx.get(f'{base}/api/version', timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        pytest.fail(f'server on {port} never answered /api/version')
    try:
        yield port, home, base
    finally:
        _cli(env, 'stop', '--port', str(port))


def _mcp_call(entry: dict, tool: str) -> dict:
    """Run the MCP entry Claude Code would run and call one tool."""
    proc = subprocess.Popen(
        [entry['command'], *entry['args']],
        cwd=entry.get('cwd') or str(REPO),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    msgs = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {
            'jsonrpc': '2.0',
            'id': 2,
            'method': 'tools/call',
            'params': {'name': tool, 'arguments': {}},
        },
    ]
    try:
        out, _ = proc.communicate(
            ''.join(json.dumps(m) + '\n' for m in msgs), timeout=60
        )
    finally:
        if proc.poll() is None:
            proc.kill()
    replies = [json.loads(line) for line in out.splitlines() if line.strip()]
    return next(r for r in replies if r.get('id') == 2)


def test_cli_started_server_gives_its_agents_its_own_port(cli_server):
    port, home, base = cli_server
    with httpx.Client(base_url=base, timeout=30) as client:
        client.post(
            '/api/auth/login',
            json={'identifier': 'admin', 'password': 'admin'},
        ).raise_for_status()
        task = client.post('/api/tasks', json={'title': 'port check'}).json()

    mcp_json = home / 'tasks' / task['id'] / '.mcp.json'
    deadline = time.monotonic() + 60
    while not mcp_json.exists() and time.monotonic() < deadline:
        time.sleep(0.5)
    assert mcp_json.exists(), f'no .mcp.json for task {task["id"]}'
    entry = json.loads(mcp_json.read_text())['mcpServers']['vibe-seller']

    args = entry['args']
    assert args[args.index('--port') + 1] == str(port)

    reply = _mcp_call(entry, 'vibe_seller_list_stores')
    text = reply['result']['content'][0]['text']
    assert 'All connection attempts failed' not in text
    assert isinstance(json.loads(text), list), text
