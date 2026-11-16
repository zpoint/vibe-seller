"""The REAL Claude Code CLI, run with the flags our launches compose.

A fake Anthropic endpoint (stdlib HTTP server) records every Messages
request, so the test sees exactly what a provider would receive: which
endpoint the CLI called, the system prompt, the tool list. No model
credits are spent.

The fake machine user (HOME + ``~/.claude``) carries what a cc-switch
user has: ``ANTHROPIC_*`` in ``settings.json`` env pointing at the
"cc-switch" endpoint, a private ``CLAUDE.md`` + rules, a ``~/CLAUDE.md``,
and a user-scope MCP server in ``~/.claude.json``. The task workspace
sits under HOME, as in production, with its own ``CLAUDE.md``.
"""

import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest

from app.ai.agent_proxy import launch_settings
from app.ai.claude_backend_utils import (
    mcp_launch_flags,
    resolve_claude_binary,
)
from app.ai.claude_isolation import (
    isolation_settings,
    read_user_mcp_servers,
    setting_sources_flags,
)

MARK_USER = 'MARKER-USER-CLAUDE-MD-7f3a'
MARK_RULE = 'MARKER-USER-RULE-91bc'
MARK_HOME = 'MARKER-HOME-CLAUDE-MD-2d4e'
MARK_WS = 'MARKER-WORKSPACE-CLAUDE-MD-5e6f'

MCP_SERVER = r"""
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if 'id' not in msg:
        continue
    method = msg.get('method')
    if method == 'initialize':
        result = {'protocolVersion': msg['params']['protocolVersion'],
                  'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'marker', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'marker_ping',
                             'description': 'ping',
                             'inputSchema': {'type': 'object'}}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': msg['id'],
                                 'result': result}) + '\n')
    sys.stdout.flush()
"""


def _claude_bin() -> str | None:
    """The binary our launches spawn, or None when none is installed."""
    found = resolve_claude_binary()
    return found if os.path.isabs(found) or shutil.which(found) else None


CLAUDE = _claude_bin()
pytestmark = [
    pytest.mark.skipif(CLAUDE is None, reason='Claude Code CLI not installed'),
    pytest.mark.skipif(sys.platform == 'win32', reason='POSIX HOME layout'),
]


class _Recorder(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header('content-type', ctype)
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(b'{}', 'application/json')

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('content-length') or 0))
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        self.server.requests.append({'path': self.path, 'body': body})
        if 'count_tokens' in self.path:
            self._send(b'{"input_tokens": 1}', 'application/json')
            return
        msg = {
            'id': 'msg_fake',
            'type': 'message',
            'role': 'assistant',
            'model': body.get('model', 'fake'),
            'content': [],
            'stop_reason': None,
            'stop_sequence': None,
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        }
        if not body.get('stream'):
            msg['content'] = [{'type': 'text', 'text': 'OK'}]
            msg['stop_reason'] = 'end_turn'
            self._send(json.dumps(msg).encode(), 'application/json')
            return
        events = [
            {'type': 'message_start', 'message': msg},
            {
                'type': 'content_block_start',
                'index': 0,
                'content_block': {'type': 'text', 'text': ''},
            },
            {
                'type': 'content_block_delta',
                'index': 0,
                'delta': {'type': 'text_delta', 'text': 'OK'},
            },
            {'type': 'content_block_stop', 'index': 0},
            {
                'type': 'message_delta',
                'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
                'usage': {'output_tokens': 1},
            },
            {'type': 'message_stop'},
        ]
        sse = ''.join(
            f'event: {e["type"]}\ndata: {json.dumps(e)}\n\n' for e in events
        )
        self._send(sse.encode(), 'text/event-stream')


@pytest.fixture
def endpoints():
    servers = {}
    for name in ('cc_switch', 'profile'):
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Recorder)
        srv.requests = []
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers[name] = srv
    yield servers
    for srv in servers.values():
        srv.shutdown()


def _url(srv) -> str:
    return f'http://127.0.0.1:{srv.server_address[1]}'


@pytest.fixture
def machine(tmp_path, endpoints):
    """A fake machine user with a cc-switch setup; returns paths."""
    root = Path(os.path.realpath(tmp_path))
    home = root / 'home'
    cfg = home / '.claude'
    (cfg / 'rules').mkdir(parents=True)
    (cfg / 'CLAUDE.md').write_text(f'{MARK_USER}\n')
    (cfg / 'rules' / 'private.md').write_text(f'{MARK_RULE}\n')
    (home / 'CLAUDE.md').write_text(f'{MARK_HOME}\n')
    (cfg / 'settings.json').write_text(
        json.dumps({
            'env': {
                'ANTHROPIC_BASE_URL': _url(endpoints['cc_switch']),
                'ANTHROPIC_AUTH_TOKEN': 'cc-switch-token',
                'ANTHROPIC_MODEL': 'cc-switch-model',
            }
        })
    )
    server = root / 'marker_mcp.py'
    server.write_text(MCP_SERVER)
    (home / '.claude.json').write_text(
        json.dumps({
            'mcpServers': {
                'marker': {
                    'type': 'stdio',
                    'command': sys.executable,
                    'args': [str(server)],
                }
            }
        })
    )
    workspace = home / '.vibe-seller'
    task_dir = workspace / 'tasks' / 'task-1'
    task_dir.mkdir(parents=True)
    (workspace / 'CLAUDE.md').write_text(f'{MARK_WS}\n')
    (task_dir / 'CLAUDE.md').symlink_to(workspace / 'CLAUDE.md')
    subprocess.run(['git', 'init', '-q', str(task_dir)], check=True)
    return {'root': root, 'home': home, 'ws': workspace, 'task': task_dir}


def _base_env(home: Path) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(('ANTHROPIC_', 'CLAUDE'))
        and k.lower() not in ('http_proxy', 'https_proxy', 'all_proxy')
    }
    env.update(
        HOME=str(home),
        NO_PROXY='127.0.0.1,localhost',
        no_proxy='127.0.0.1,localhost',
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1',
        DISABLE_AUTOUPDATER='1',
        CLAUDE_CODE_DISABLE_AUTO_MEMORY='1',
    )
    return env


def _run(machine, profile_id: str, env: dict, load_global: bool) -> None:
    """Launch the CLI the way AgentSession composes it."""
    settings = {
        **launch_settings(),
        **isolation_settings(machine['ws'], env),
    }
    run_dir = machine['root'] / f'run-{profile_id}'
    run_dir.mkdir()
    settings_file = run_dir / 'settings.json'
    settings_file.write_text(json.dumps(settings))
    user_cfg = None
    if load_global and profile_id != 'default':
        user_cfg = run_dir / 'user-mcp.json'
        user_cfg.write_text(
            json.dumps({'mcpServers': read_user_mcp_servers(env)})
        )
    cmd = [
        CLAUDE,
        '-p',
        'Reply OK',
        '--settings',
        str(settings_file),
        *setting_sources_flags(profile_id),
        *mcp_launch_flags(
            load_global=load_global,
            session_config=None,
            project_config=None,
            user_config=user_cfg,
        ),
    ]
    proc = subprocess.run(
        cmd,
        cwd=machine['task'],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]


def _messages(srv) -> list[dict]:
    return [
        r['body'] for r in srv.requests if r['path'].startswith('/v1/messages')
    ]


def _assert_only_workspace_memory(requests: list[dict]) -> None:
    assert requests
    blob = json.dumps(requests)
    for mark in (MARK_USER, MARK_RULE, MARK_HOME):
        assert mark not in blob, f'{mark} reached the provider'
    assert MARK_WS in blob, 'the workspace CLAUDE.md must still load'


def test_non_default_profile_ignores_cc_switch_and_user_memory(
    machine, endpoints
):
    env = _base_env(machine['home'])
    env.update(
        ANTHROPIC_BASE_URL=_url(endpoints['profile']),
        ANTHROPIC_AUTH_TOKEN='profile-token',
        ANTHROPIC_MODEL='profile-model',
    )
    _run(machine, 'deepseek', env, load_global=False)

    assert _messages(endpoints['cc_switch']) == []
    sent = _messages(endpoints['profile'])
    assert {b.get('model') for b in sent} == {'profile-model'}
    _assert_only_workspace_memory(sent)


def test_default_profile_uses_cc_switch_without_user_memory(machine, endpoints):
    _run(machine, 'default', _base_env(machine['home']), load_global=False)

    assert _messages(endpoints['profile']) == []
    sent = _messages(endpoints['cc_switch'])
    assert {b.get('model') for b in sent} == {'cc-switch-model'}
    _assert_only_workspace_memory(sent)


@pytest.mark.parametrize('load_global', [True, False])
def test_global_mcp_toggle_with_the_user_source_off(
    machine, endpoints, load_global
):
    env = _base_env(machine['home'])
    env.update(
        ANTHROPIC_BASE_URL=_url(endpoints['profile']),
        ANTHROPIC_AUTH_TOKEN='profile-token',
        ANTHROPIC_MODEL='profile-model',
    )
    _run(machine, 'deepseek', env, load_global=load_global)

    sent = _messages(endpoints['profile'])
    tools = {t.get('name') for b in sent for t in b.get('tools') or []}
    # The CLI may defer MCP tools behind a tool-search tool; either way
    # the server's tool name reaches the request.
    seen = 'marker_ping' in json.dumps(sent)
    assert seen is load_global, sorted(tools)
