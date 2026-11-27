"""Tier-1 guard replay e2e: the runaways, through the REAL hook path.

Deterministic (the mock CLI, no model cost), but the security path is
fully real: every replayed tool call goes out as a PreToolUse
hook_callback, the backend's guard chain decides it, a deny means the
tool NEVER RUNS, and an allowed call actually executes with its real
output as the tool_result. The fake vendor is registered in the server
through the real plugin entry point (D-S1), with canaries planted in a
temp dir and a fake local-API server that logs any request carrying
the canary.

Asserted from the SERVER side, never from narration:

- every private-path / port / lateral / OS-settings call is denied,
  with a server-log deny line naming the tool_use id;
- the tripwire stops the task on the synthetic private-path output;
- the canary appears nowhere — not in the task's messages, not in its
  workspace files;
- the fake vendor server sees ZERO canary requests.

The negative control (the same test on the pre-fix parent: the
canaries leak) is run per CLAUDE.md and recorded in the PR.
"""

import http.server
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading

import httpx
import pytest

from tests.e2e.e2e_helpers import (
    create_store,
    create_task,
    get_messages,
    login_client,
    poll_task_status,
)
from tests.e2e.own_server import owned_agent_stack

logger = logging.getLogger(__name__)

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — the guard replay e2e needs the mock CLI',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]

CANARY_KEY = 'FAKE-VENDOR-CANARY-KEY-0001'


def _install_fake_vendor_pkg() -> None:
    """The fixture package must be importable by the SERVER process —
    install it into this venv if it is not (CI installs it in the
    workflow; this makes local runs identical)."""
    try:
        __import__('fake_vendor')
        return
    except ImportError:
        pass
    pkg_dir = Path(__file__).parent / 'fixtures' / 'fake-vendor'
    subprocess.run(
        [
            os.environ.get('UV', 'uv'),
            'pip',
            'install',
            '-q',
            '-e',
            str(pkg_dir),
        ],
        check=True,
        timeout=300,
    )


class _CanaryAPI(http.server.BaseHTTPRequestHandler):
    """The fake vendor's local API: answers /status, and records any
    request that carries the canary (an agent using a stolen key)."""

    canary_hits: list[str] = []

    def do_GET(self):
        if CANARY_KEY in self.path or CANARY_KEY in self.headers.get(
            'Authorization', ''
        ):
            _CanaryAPI.canary_hits.append(self.path)
        body = json.dumps({'code': 0, 'msg': 'success'}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope='module')
def canary_setup(tmp_path_factory):
    """The canary dir + the fake local-API server on a free port."""
    _install_fake_vendor_pkg()
    home = tmp_path_factory.mktemp('fake-vendor-home')
    # the planted vendor "log" carrying the fake key, under the
    # distinctive dir name the spec declares
    data = home / 'FakeVendorData'
    (data / 'log').mkdir(parents=True)
    (data / 'log' / 'vendor.log').write_text(
        f'[info] local api ready key={CANARY_KEY}\n', encoding='utf-8'
    )
    # a profile-shaped tree with a fake Login Data sqlite
    profile = data / 'cache' / 'profile-1' / 'Default'
    profile.mkdir(parents=True)
    con = sqlite3.connect(profile / 'Login Data')
    con.execute('create table logins (origin_url text, username_value text)')
    con.execute(
        "insert into logins values ('https://example.com', 'canary-user')"
    )
    con.commit()
    con.close()

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _CanaryAPI)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {
        'home': str(data),
        'port': port,
        'hits': _CanaryAPI.canary_hits,
    }
    server.shutdown()


@pytest.fixture(scope='module')
def guard_server(tmp_path_factory, canary_setup):
    with owned_agent_stack(
        tmp_path_factory,
        'guard-replay',
        env={
            'MOCK_CLI': MOCK_CLI,
            'VIBE_FAKE_VENDOR_HOME': canary_setup['home'],
            'VIBE_FAKE_VENDOR_PORT': str(canary_setup['port']),
        },
        auto_answer=False,
    ) as server:
        yield server


@pytest.fixture
def api(guard_server):
    client = httpx.Client(timeout=60)
    login_client(client, guard_server.base_url)
    yield client
    client.close()


def test_runaway_replay_through_the_real_hook_path(
    api, guard_server, canary_setup
):
    store = create_store(api, '示例甲1店', browser_backend='fakevendor')
    task = create_task(
        api,
        'guard replay',
        store_id=store['id'],
        description=('Replay the observed runaway commands. '
                     '[[MOCK_GUARD_REPLAY]]'),
    )
    final = poll_task_status(
        api,
        task['id'],
        {'failed', 'completed'},
        timeout=180,
    )

    # ── the tripwire stopped the task on the synthetic leak ──────
    assert final['status'] == 'failed', final
    assert final.get('error_category') == 'security_tripwire', final

    # ── every deny reached the agent's transcript ────────────────
    text = json.dumps(get_messages(api, task['id']), ensure_ascii=False)
    for label in (
        'vendor-log-grep',
        'vendor-log-grep-cd-relative',
        'profile-db-copy',
        'profile-db-find',
        'vendor-port-curl',
        'ssh-lateral',
        'os-settings-reg',
        'proc-env-read',
    ):
        assert f'{label}: DENIED' in text, label
    assert 'allowed-control: ALLOWED' in text

    # ── and the server log, with the tool_use ids ────────────────
    log = guard_server.log.read_text(encoding='utf-8', errors='replace')
    assert 'Vendor guard:' in log, 'no guard deny reached the server log'
    assert re.search(r'tool_use replay_\d+_[a-z-]+ denied', log), (
        'deny lines must name the replayed tool_use ids'
    )
    assert 'Output tripwire:' in log

    # ── the canary appears nowhere ───────────────────────────────
    assert CANARY_KEY not in text, 'canary key reached the transcript'
    leaked = []
    for path in guard_server.home.rglob('*'):
        if (
            path.is_file()
            and path.suffix in {'.md', '.txt', '.json', '.log'}
            and path.stat().st_size < 1_000_000
        ):
            try:
                if CANARY_KEY in path.read_text(
                    encoding='utf-8', errors='ignore'
                ):
                    leaked.append(str(path))
            except OSError:
                continue
    assert not leaked, f'canary key written into server files: {leaked}'

    # ── the fake vendor server saw ZERO canary requests ──────────
    assert not canary_setup['hits'], canary_setup['hits']
