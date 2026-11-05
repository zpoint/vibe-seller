"""E2E: the MCP workspace tools — ``workspace_file``, ``manage_skill``
and ``workspace_assistant``.

Companion to ``test_agent_mcp.py``; same mock-CLI lane (no model
credits) and the same skip guard. A SEPARATE FILE on purpose: the
e2e-mock-cli CI job names its test files explicitly.

These run against the server's REAL workspace: the same git-backed
tree the web UI's Workspace view edits. What they pin:

* ``workspace_file`` — write lands (and is committed, so history /
  version / reset work on the SAME commits a UI write produces);
  the tree lists only the editable roots; escapes are refused;
* ``manage_skill`` — a user-space skill's whole life over MCP, through
  the same routes the Skills view calls (reserved names refused);
* ``workspace_assistant`` — the per-user assistant chat loop. Under
  MOCK_CLI the assistant spawns the mock too (``app/ai/
  workspace_assistant.py`` takes the same test branch the task agent
  does), so the send → reply turn runs without model credits.
"""

import logging
import os
import uuid

import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.test_agent_mcp import McpClient

logger = logging.getLogger(__name__)

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — skipping MCP e2e tests. '
        'Run with: MOCK_CLI=tests/e2e/mock_cli.py ./start.sh 7777',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]

SKILL_SLUG = 'mcp-e2e-skill'


# ── Fixtures ────────────────────────────────────────


@pytest.fixture(scope='module')
def api_token(api_client):
    """A minted token, with auth ON for the module (mirrors
    test_agent_mcp)."""
    resp = api_client.put(
        f'{BASE_URL}/api/settings', json={'auth_required': 'true'}
    )
    resp.raise_for_status()

    created = api_client.post(
        f'{BASE_URL}/api/auth/tokens',
        json={'name': 'e2e-mcp-workspace', 'expires_in_days': 1},
    )
    created.raise_for_status()
    body = created.json()
    try:
        yield {'token': body['token'], 'id': body['id']}
    finally:
        api_client.delete(f'{BASE_URL}/api/auth/tokens/{body["id"]}')
        api_client.put(
            f'{BASE_URL}/api/settings', json={'auth_required': 'false'}
        )


@pytest.fixture(scope='module')
def mcp(api_token) -> McpClient:
    client = McpClient(token=api_token['token'])
    yield client
    client.close()


# ── workspace_file ───────────────────────────────────


class TestWorkspaceFile:
    def test_tree_lists_only_the_editable_roots(self, mcp):
        listed = mcp.call_tool('workspace_file', action='tree')
        assert listed['ok'], listed['text']
        paths = [f['path'] for f in listed['data']['files']]
        assert paths, 'the workspace has no editable files at all'
        assert all(
            p.startswith(('knowledge/', 'stores/', '.claude/skills/'))
            for p in paths
        ), paths

        scoped = mcp.call_tool(
            'workspace_file', action='tree', prefix='knowledge'
        )
        assert scoped['ok'], scoped['text']
        assert all(
            f['path'].startswith('knowledge/') for f in scoped['data']['files']
        )

    def test_structured_has_the_workspace_sections(self, mcp):
        structured = mcp.call_tool('workspace_file', action='structured')
        assert structured['ok'], structured['text']
        assert 'skills' in structured['data']

    def test_write_read_history_version_reset(self, mcp):
        """The audited-edit loop: two writes, the git history between
        them, the old version at its commit, and a reset back to it.

        The contents are per-run, so the commits this test makes are
        distinguishable from any earlier run's.
        """
        path = 'knowledge/mcp-e2e-notes.md'
        v1 = f'first note {uuid.uuid4().hex}'
        v2 = f'second note {uuid.uuid4().hex}'

        written = mcp.call_tool(
            'workspace_file', action='write', path=path, content=v1
        )
        assert written['ok'], written['text']

        read = mcp.call_tool('workspace_file', action='read', path=path)
        assert read['ok'], read['text']
        assert read['data']['content'] == v1
        assert read['data']['truncated'] is False

        history = mcp.call_tool(
            'workspace_file', action='history', path=path, max_count=20
        )
        assert history['ok'], history['text']
        commits = history['data']['commits']
        assert commits, 'the write was not committed to the history'
        first_sha = commits[0]['sha']
        assert commits[0]['message']

        mcp.call_tool('workspace_file', action='write', path=path, content=v2)
        read2 = mcp.call_tool('workspace_file', action='read', path=path)
        assert read2['data']['content'] == v2

        # The version at the first write's commit is the first note —
        # the audit trail a UI write produces, readable over MCP.
        version = mcp.call_tool(
            'workspace_file',
            action='version',
            path=path,
            commit=first_sha,
        )
        assert version['ok'], version['text']
        assert version['data']['content'] == v1

        reset = mcp.call_tool(
            'workspace_file', action='reset', path=path, commit=first_sha
        )
        assert reset['ok'], reset['text']
        after = mcp.call_tool('workspace_file', action='read', path=path)
        assert after['data']['content'] == v1

        # Leave the tree as it was found.
        deleted = mcp.call_tool('workspace_file', action='delete', path=path)
        assert deleted['ok'], deleted['text']

    def test_escapes_and_outside_paths_are_refused(self, mcp):
        for bad in (
            'CLAUDE.md',
            'tasks/t.md',
            'data/db.sqlite',
            'knowledge/../CLAUDE.md',
            '/etc/passwd',
            'knowledge\\..\\x',
        ):
            refused = mcp.call_tool('workspace_file', action='read', path=bad)
            assert not refused['ok'], bad

    def test_write_without_content_is_rejected(self, mcp):
        refused = mcp.call_tool(
            'workspace_file', action='write', path='knowledge/x.md'
        )
        assert not refused['ok']
        assert 'content' in refused['text']

    def test_unknown_action_is_rejected(self, mcp):
        # Refused by the dispatcher's schema validation (INVALID_PARAMS),
        # naming the bad value and the valid choices.
        error = mcp.call_tool_rejected('workspace_file', action='chmod')
        assert error['code'] == -32602
        assert 'chmod' in error['message']
        assert 'read' in error['message']


# ── manage_skill ─────────────────────────────────────


class TestManageSkill:
    @pytest.fixture(autouse=True)
    def _clean_slug(self, mcp):
        """Delete a leftover skill from an earlier (crashed) run so the
        create below is deterministic; delete again after the test."""
        mcp.call_tool('manage_skill', action='delete', slug=SKILL_SLUG)
        yield
        mcp.call_tool('manage_skill', action='delete', slug=SKILL_SLUG)

    def test_create_get_update_delete(self, mcp):
        created = mcp.call_tool(
            'manage_skill',
            action='create',
            name=SKILL_SLUG,
            description='MCP e2e scratch skill',
        )
        assert created['ok'], created['text']
        assert created['data']['path'] == f'.claude/skills/{SKILL_SLUG}'

        listed = mcp.call_tool('manage_skill', action='list')
        assert listed['ok'], listed['text']
        entry = next(
            s for s in listed['data']['skills'] if s['slug'] == SKILL_SLUG
        )
        assert entry['source'] == 'custom'

        got = mcp.call_tool('manage_skill', action='get', slug=SKILL_SLUG)
        assert got['ok'], got['text']
        assert 'MCP e2e scratch skill' in got['data']['skill_md']
        assert got['data']['files'], got['data']

        updated = mcp.call_tool(
            'manage_skill',
            action='update',
            slug=SKILL_SLUG,
            skill_md=(
                '---\n'
                f'name: {SKILL_SLUG}\n'
                'description: v2\n'
                '\n'
                '# Scratch skill\n\n'
                'Revised body.\n'
            ),
            files={'helper.py': 'print("helper")\n'},
        )
        assert updated['ok'], updated['text']

        got2 = mcp.call_tool('manage_skill', action='get', slug=SKILL_SLUG)
        assert 'Revised body.' in got2['data']['skill_md']
        assert (
            f'.claude/skills/{SKILL_SLUG}/helper.py' in (got2['data']['files'])
        )

        deleted = mcp.call_tool(
            'manage_skill', action='delete', slug=SKILL_SLUG
        )
        assert deleted['ok'], deleted['text']
        assert deleted['data']['status'] == 'deleted'

        gone = mcp.call_tool('manage_skill', action='get', slug=SKILL_SLUG)
        assert not gone['ok']
        assert 'not found' in gone['text'].lower()

    def test_reserved_name_is_refused(self, mcp):
        refused = mcp.call_tool(
            'manage_skill', action='create', name='_builtin'
        )
        assert not refused['ok']
        assert 'reserved' in refused['text'].lower()

    def test_update_without_skill_md_is_rejected(self, mcp):
        refused = mcp.call_tool(
            'manage_skill', action='update', slug='anything'
        )
        assert not refused['ok']
        assert 'skill_md' in refused['text']


# ── workspace_assistant ──────────────────────────────


class TestWorkspaceAssistant:
    def test_status_and_stop_when_idle(self, mcp):
        status = mcp.call_tool('workspace_assistant', action='status')
        assert status['ok'], status['text']
        assert status['data']['running'] is False
        assert 'last_reply' in status['data']

        stopped = mcp.call_tool('workspace_assistant', action='stop')
        assert stopped['ok'], stopped['text']
        assert stopped['data']['ok'] is False
        assert 'No assistant session' in stopped['data']['note']

    def test_send_returns_the_reply(self, mcp):
        """One assistant turn, end to end. Under MOCK_CLI the assistant
        runs the mock CLI (the same test branch the task agent takes),
        whose result text is the deterministic reply."""
        sent = mcp.call_tool(
            'workspace_assistant',
            action='send',
            message='Tidy the knowledge notes.',
            wait_seconds=60,
        )
        assert sent['ok'], sent['text']
        data = sent['data']
        assert data['ok'] is True
        assert data['done'] is True, data
        assert data['timed_out'] is False
        assert data['reply'], data

        # The manager's own state is the independent check.
        status = mcp.call_tool('workspace_assistant', action='status')
        assert status['data']['running'] is False
        assert status['data']['last_reply'] == data['reply']

    def test_empty_message_is_rejected(self, mcp):
        refused = mcp.call_tool(
            'workspace_assistant', action='send', message='   '
        )
        assert not refused['ok']
        assert 'message' in refused['text']

    def test_unknown_action_is_rejected(self, mcp):
        # Refused by the dispatcher's schema validation (INVALID_PARAMS),
        # naming the bad value and the valid choices.
        error = mcp.call_tool_rejected('workspace_assistant', action='restart')
        assert error['code'] == -32602
        assert 'restart' in error['message']
        assert 'send' in error['message']
