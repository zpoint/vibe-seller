"""Workflow tests for workspace commit attribution over real HTTP.

A REAL WorkspaceManager (fresh git repo, no venv build) is patched
into the sites the dict-mock usually takes, so every write below goes
through the real route → real manager → real ``git commit``. What
these pin (the follow-up-1 invariant):

* a web-UI session write is authored by the signed-in user with
  ``Via: session`` in the message;
* an MCP write authenticated by a ``vst_`` token is authored by the
  token's owner with ``Via: api_token``;
* a task pipeline's own knowledge commit keeps the agent identity —
  whoever launched the task;
* back-to-back callers do not leak into each other's commits.
"""

import json

import pytest
import pytest_asyncio

from app.workspace.manager import WorkspaceManager
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow

MCP_PATH = '/api/mcp'

# The same import sites the mock_workspace fixture patches — the real
# manager must take every one of them back, or a route would still
# speak to the mock.
_PATCH_SITES = [
    'app.routers.workspace.workspace_manager',
    'app.routers.stores.workspace_manager',
    'app.routers.tasks_conversation.workspace_manager',
    'app.task_runner.workspace_manager',
    'app.task_runner_auto.workspace_manager',
    'app.task_runner_exec.workspace_manager',
    'app.scheduler.finalize_reaper.workspace_manager',
    'app.agent_mcp.tools_workspace.workspace_manager',
]


@pytest_asyncio.fixture
async def real_workspace(monkeypatch, tmp_path, admin_client):
    """A real manager over a fresh git repo, pre-initialized OUTSIDE
    any request (so the scaffold commit is the agent's, like boot)."""
    mgr = WorkspaceManager(root=tmp_path)

    async def _no_venv():
        return None

    mgr._ensure_venv = _no_venv  # type: ignore[method-assign]
    await mgr.ensure_init(create_venv=False)
    for site in _PATCH_SITES:
        monkeypatch.setattr(site, mgr)
    return mgr


async def _history(client, path):
    r = await client.get('/api/workspace/file/history', params={'path': path})
    assert r.status_code == 200, r.text
    return r.json()['commits']


async def _mcp(client, name, arguments, token=None):
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    r = await client.post(
        MCP_PATH,
        json={
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments},
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert 'error' not in body, body
    text = body['result']['content'][0]['text']
    assert not body['result'].get('isError'), text
    return json.loads(text)


async def _mint_token(admin_client):
    minted = await admin_client.post(
        '/api/auth/tokens', json={'name': 'attribution-test'}
    )
    assert minted.status_code == 200, minted.text
    return minted.json()['token']


class TestAttribution:
    async def test_ui_session_write(
        self, admin_client, install_fake_agent, real_workspace
    ):
        r = await admin_client.put(
            '/api/workspace/file',
            params={'path': 'knowledge/from-ui.md'},
            json={'content': 'written in the browser'},
        )
        assert r.status_code == 200, r.text

        commits = await _history(admin_client, 'knowledge/from-ui.md')
        assert commits[0]['author'] == 'admin'
        assert 'Via: session' in commits[0]['message']

    async def test_mcp_token_write(
        self,
        admin_client,
        unauthed_client,
        admin_user,
        install_fake_agent,
        real_workspace,
    ):
        token = await _mint_token(admin_client)
        result = await _mcp(
            unauthed_client,
            'workspace_file',
            {
                'action': 'write',
                'path': 'knowledge/from-mcp.md',
                'content': 'written by an external agent',
            },
            token=token,
        )
        assert result['status'] == 'ok'

        commits = await _history(admin_client, 'knowledge/from-mcp.md')
        assert commits[0]['author'] == 'admin'
        assert 'Via: api_token' in commits[0]['message']
        # The MCP history action shows the same attribution.
        via_mcp = await _mcp(
            admin_client,
            'workspace_file',
            {'action': 'history', 'path': 'knowledge/from-mcp.md'},
        )
        assert via_mcp['commits'][0]['author'] == 'admin'
        assert 'Via: api_token' in via_mcp['commits'][0]['message']

    async def test_task_pipeline_commit_stays_the_agents(
        self, admin_client, install_fake_agent, real_workspace
    ):
        """The pipeline's 'Knowledge update after task' sweep is the
        task AGENT's work: launched from an authenticated request, its
        commits still carry the agent identity — the orchestrator
        entry (reset_ledger) drops the caller's context."""
        # The store first: creating one commits its profile scaffold
        # in-request (attributed to the caller — correct, and not the
        # commit under test here).
        store = await admin_client.post(
            '/api/stores', json={'name': 'Attribution Store'}
        )
        # An uncommitted knowledge change for the AGENT's sweep to
        # pick up, written after the store so no other commit can
        # claim it.
        (real_workspace.root / 'knowledge' / 'swept.md').write_text(
            'agent knowledge'
        )
        created = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Sweep me',
                'description': 'x',
                'store_id': store.json()['id'],
            },
        )
        task_id = created.json()['id']
        await wait_for_task(admin_client, task_id)

        commits = await _history(admin_client, 'knowledge/swept.md')
        assert commits, 'the pipeline never committed the sweep'
        assert commits[0]['author'] == 'Vibe Seller'
        assert 'Knowledge update after task' in commits[0]['message']
        assert 'Via:' not in commits[0]['message']

    async def test_back_to_back_callers_do_not_leak(
        self,
        admin_client,
        member_client,
        install_fake_agent,
        real_workspace,
        override_async_session,
    ):
        r = await admin_client.put(
            '/api/workspace/file',
            params={'path': 'knowledge/who.md'},
            json={'content': 'first'},
        )
        assert r.status_code == 200, r.text
        m = await member_client.put(
            '/api/workspace/file',
            params={'path': 'knowledge/who.md'},
            json={'content': 'second'},
        )
        assert m.status_code == 200, m.text

        commits = await _history(admin_client, 'knowledge/who.md')
        # Newest first: the member's commit, the admin's — each named,
        # neither bleeding into the other.
        assert commits[0]['author'] == 'member'
        assert 'Via: session' in commits[0]['message']
        assert commits[1]['author'] == 'admin'
        assert 'Via: session' in commits[1]['message']
