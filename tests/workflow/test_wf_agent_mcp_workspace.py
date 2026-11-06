"""Workflow tests for the MCP workspace tools: ``workspace_file`` and
``manage_skill``.

Real HTTP → the real route handlers over the mock workspace (the same
fixture the workspace REST tests use). What these pin:

* every action reaches the same route the Workspace view calls
  (tree/read/write/delete/history/version/reset, structured,
  store-profile, knowledge sync status/meta/run);
* path confinement: escapes are refused BEFORE anything resolves, and
  paths outside the editable roots are refused even when they exist;
* a write lands in the same audited git history a UI write does
  (the mock asserts the auto-commit call);
* skills: list/get/create/update/delete through the skills routes,
  with the route's own guards (reserved names, built-in slugs).
"""

import base64
import json
import os

import pytest

from app.sync.scheduler import (
    Cadence,
    SyncJob,
    SyncResult,
    sync_scheduler,
)

pytestmark = pytest.mark.workflow

MCP_PATH = '/api/mcp'

# 1x1 PNG — a genuinely binary file (invalid UTF-8).
PNG_B64 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
    'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
)


async def rpc(client, method, params=None, request_id=1):
    payload = {'jsonrpc': '2.0', 'id': request_id, 'method': method}
    if params is not None:
        payload['params'] = params
    return await client.post(MCP_PATH, json=payload)


async def call_tool(client, name, arguments=None):
    r = await rpc(
        client, 'tools/call', {'name': name, 'arguments': arguments or {}}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert 'error' not in body, body
    result = body['result']
    text = result['content'][0]['text']
    if result.get('isError'):
        return {'error': text}, result
    return (json.loads(text) if text else {}), result


@pytest.fixture
async def sync_jobs_ready():
    """Sync jobs registered for the duration of one test.

    The manual sync the MCP tools offer is the scheduler's manual run,
    so the jobs have to exist for it to run at all.
    """

    async def run(ctx):
        return SyncResult(status='updated', version='a' * 40)

    sync_scheduler.reset()
    for name in ('skills', 'knowledge'):
        await sync_scheduler.register(
            SyncJob(name=name, cadence=Cadence.DAILY, run=run)
        )
    yield
    sync_scheduler.reset()


class TestWorkspaceFile:
    async def test_tree_lists_only_editable_roots(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        (mock_workspace.root / 'knowledge').mkdir(exist_ok=True)
        (mock_workspace.root / 'knowledge' / 'catalog.md').write_text('notes')
        (mock_workspace.root / 'tasks').mkdir(exist_ok=True)
        (mock_workspace.root / 'tasks' / 't1.md').write_text('task file')

        # No action: the dispatcher rejects it before the tool runs.
        missing = await rpc(
            admin_client,
            'tools/call',
            {'name': 'workspace_file', 'arguments': {}},
        )
        assert missing.json()['error']['code'] == -32602

        data, _ = await call_tool(
            admin_client, 'workspace_file', {'action': 'tree'}
        )
        paths = [f['path'] for f in data['files']]
        assert 'knowledge/catalog.md' in paths
        assert all(
            p.startswith(('knowledge/', 'stores/', '.claude/skills/'))
            for p in paths
        ), paths
        assert 'tasks/t1.md' not in paths

        scoped, _ = await call_tool(
            admin_client,
            'workspace_file',
            {'action': 'tree', 'prefix': 'knowledge'},
        )
        assert [f['path'] for f in scoped['files']] == ['knowledge/catalog.md']

    async def test_read_and_write_round_trip(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        (mock_workspace.root / 'stores' / 'acme').mkdir(parents=True)
        (mock_workspace.root / 'stores' / 'acme' / 'notes.md').write_text(
            'old notes'
        )

        written, envelope = await call_tool(
            admin_client,
            'workspace_file',
            {
                'action': 'write',
                'path': 'stores/acme/notes.md',
                'content': 'new notes',
            },
        )
        assert envelope['isError'] is False, envelope
        assert written['status'] == 'ok'
        # The write went through the manager (the route's own guard
        # and commit), not around it.
        assert (
            mock_workspace.root / 'stores' / 'acme' / 'notes.md'
        ).read_text() == 'new notes'

        read, _ = await call_tool(
            admin_client,
            'workspace_file',
            {'action': 'read', 'path': 'stores/acme/notes.md'},
        )
        assert read['content'] == 'new notes'
        assert read['truncated'] is False

    async def test_delete_removes_the_file(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        (mock_workspace.root / 'knowledge').mkdir(exist_ok=True)
        (mock_workspace.root / 'knowledge' / 'scratch.md').write_text('x')
        deleted, envelope = await call_tool(
            admin_client,
            'workspace_file',
            {'action': 'delete', 'path': 'knowledge/scratch.md'},
        )
        assert envelope['isError'] is False, envelope
        assert deleted['status'] == 'deleted'
        assert not (mock_workspace.root / 'knowledge' / 'scratch.md').exists()

    async def test_history_version_reset(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        (mock_workspace.root / 'knowledge').mkdir(exist_ok=True)
        target = mock_workspace.root / 'knowledge' / 'catalog.md'
        target.write_text('v1')

        history, _ = await call_tool(
            admin_client,
            'workspace_file',
            {'action': 'history', 'path': 'knowledge/catalog.md'},
        )
        assert history['path'] == 'knowledge/catalog.md'
        assert history['commits'] == []

        version, _ = await call_tool(
            admin_client,
            'workspace_file',
            {
                'action': 'version',
                'path': 'knowledge/catalog.md',
                'commit': 'abc123',
            },
        )
        assert version['commit'] == 'abc123'
        assert version['content'] == ''

        reset, envelope = await call_tool(
            admin_client,
            'workspace_file',
            {
                'action': 'reset',
                'path': 'knowledge/catalog.md',
                'commit': 'abc123',
            },
        )
        assert envelope['isError'] is False, envelope
        assert reset['status'] == 'ok'

    async def test_structured_and_store_profile_and_sync(
        self,
        admin_client,
        install_fake_agent,
        mock_workspace,
        mock_knowledge_sync,
        sync_jobs_ready,
    ):
        structured, envelope = await call_tool(
            admin_client, 'workspace_file', {'action': 'structured'}
        )
        assert envelope['isError'] is False, envelope
        assert 'skills' in structured

        profile, _ = await call_tool(
            admin_client,
            'workspace_file',
            {
                'action': 'store_profile',
                'slug': 'acme',
                'name': 'Acme Store',
                'platform': 'amazon',
                'country': 'SA',
            },
        )
        assert profile['path'] == 'stores/acme'
        assert (mock_workspace.root / 'stores' / 'acme' / 'STORE.md').is_file()

        synced, _ = await call_tool(
            admin_client, 'workspace_file', {'action': 'knowledge_sync'}
        )
        assert synced['name'] == 'knowledge'
        assert synced['status'] == 'updated'

        status, _ = await call_tool(
            admin_client, 'workspace_file', {'action': 'knowledge_status'}
        )
        assert status == {'synced': True, 'files': 0}

        meta, _ = await call_tool(
            admin_client, 'workspace_file', {'action': 'knowledge_meta'}
        )
        assert meta['name'] == 'knowledge'
        assert meta['last_ok_at'] is not None

    async def test_paths_outside_the_editable_roots_are_refused(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        # Real files outside the editable roots — the tool must refuse
        # them even though the manager's own guard would allow them
        # (the workspace ROOT is its boundary, not the edit roots).
        (mock_workspace.root / 'CLAUDE.md').write_text('instructions')
        (mock_workspace.root / 'tasks').mkdir(exist_ok=True)
        (mock_workspace.root / 'tasks' / 't.md').write_text('task')

        for bad in (
            'CLAUDE.md',
            'tasks/t.md',
            'data/db.sqlite',
            'knowledge/../CLAUDE.md',
            '/etc/passwd',
            'knowledge\\..\\x',
            '.venv/pyvenv.cfg',
        ):
            _, envelope = await call_tool(
                admin_client,
                'workspace_file',
                {'action': 'read', 'path': bad},
            )
            assert envelope['isError'] is True, bad
            assert 'instructions' not in envelope['content'][0]['text']

    async def test_write_without_content_is_rejected(
        self, admin_client, install_fake_agent
    ):
        _, envelope = await call_tool(
            admin_client,
            'workspace_file',
            {'action': 'write', 'path': 'knowledge/x.md'},
        )
        assert envelope['isError'] is True
        assert 'content' in envelope['content'][0]['text']

    async def test_unknown_action_is_rejected(
        self, admin_client, install_fake_agent
    ):
        """An off-enum action is refused by the DISPATCHER's schema
        validation (``INVALID_PARAMS``, naming the argument and the
        choices) — the tool's own "Unknown action" check stays as the
        second line."""
        r = await rpc(
            admin_client,
            'tools/call',
            {'name': 'workspace_file', 'arguments': {'action': 'chmod'}},
        )
        body = r.json()
        assert body['error']['code'] == -32602
        assert 'action' in body['error']['message']
        assert 'chmod' in body['error']['message']
        assert 'read' in body['error']['message']  # the choices listed

    async def test_member_has_the_same_workspace_tools(
        self, member_client, install_fake_agent, mock_workspace
    ):
        """No workspace route restricts by role — parity for members."""
        (mock_workspace.root / 'knowledge').mkdir(exist_ok=True)
        (mock_workspace.root / 'knowledge' / 'shared.md').write_text('shared')
        read, envelope = await call_tool(
            member_client,
            'workspace_file',
            {'action': 'read', 'path': 'knowledge/shared.md'},
        )
        assert envelope['isError'] is False
        assert read['content'] == 'shared'

    async def test_unauthenticated_is_401(self, unauthed_client, admin_user):
        r = await unauthed_client.post(
            MCP_PATH,
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {
                    'name': 'workspace_file',
                    'arguments': {'action': 'tree'},
                },
            },
        )
        assert r.status_code == 401


class TestManageSkill:
    async def test_create_get_update_delete(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        created, envelope = await call_tool(
            admin_client,
            'manage_skill',
            {
                'action': 'create',
                'name': 'listing-checker',
                'description': 'Checks listings for the acme store',
            },
        )
        assert envelope['isError'] is False, envelope
        assert created['path'] == '.claude/skills/listing-checker'

        listed, _ = await call_tool(
            admin_client, 'manage_skill', {'action': 'list'}
        )
        slugs = [s['slug'] for s in listed['skills']]
        assert 'listing-checker' in slugs

        got, _ = await call_tool(
            admin_client,
            'manage_skill',
            {'action': 'get', 'slug': 'listing-checker'},
        )
        assert 'Checks listings' in got['skill_md']
        assert got['source'] == 'custom'
        assert got['files'], got

        updated, update_env = await call_tool(
            admin_client,
            'manage_skill',
            {
                'action': 'update',
                'slug': 'listing-checker',
                'skill_md': (
                    '---\nname: listing-checker\ndescription: v2\n'
                    '\n# Listing checker\n\nRevised body.\n'
                ),
                'files': {'helper.py': 'print("helper")\n'},
            },
        )
        assert update_env['isError'] is False, update_env
        assert updated['action'] in ('updated', 'created')

        got2, _ = await call_tool(
            admin_client,
            'manage_skill',
            {'action': 'get', 'slug': 'listing-checker'},
        )
        assert 'Revised body.' in got2['skill_md']
        assert '.claude/skills/listing-checker/helper.py' in got2['files']

        deleted, del_env = await call_tool(
            admin_client,
            'manage_skill',
            {'action': 'delete', 'slug': 'listing-checker'},
        )
        assert del_env['isError'] is False, del_env
        assert deleted['status'] == 'deleted'

    async def test_reserved_name_is_the_routes_refusal(
        self, admin_client, install_fake_agent
    ):
        _, envelope = await call_tool(
            admin_client,
            'manage_skill',
            {
                'action': 'create',
                'name': '_builtin',
                'description': 'x',
            },
        )
        assert envelope['isError'] is True
        assert 'reserved' in envelope['content'][0]['text'].lower()

    async def test_get_a_missing_skill_is_an_error(
        self, admin_client, install_fake_agent
    ):
        _, envelope = await call_tool(
            admin_client,
            'manage_skill',
            {'action': 'get', 'slug': 'no-such-skill'},
        )
        assert envelope['isError'] is True
        assert 'not found' in envelope['content'][0]['text'].lower()

    async def test_update_without_skill_md_is_rejected(
        self, admin_client, install_fake_agent
    ):
        _, envelope = await call_tool(
            admin_client,
            'manage_skill',
            {'action': 'update', 'slug': 'anything'},
        )
        assert envelope['isError'] is True
        assert 'skill_md' in envelope['content'][0]['text']

    async def test_sync_and_sync_meta(
        self, admin_client, install_fake_agent, sync_jobs_ready
    ):
        """sync runs the scheduler's manual job; sync_meta reads its state."""
        synced, envelope = await call_tool(
            admin_client, 'manage_skill', {'action': 'sync'}
        )
        assert envelope['isError'] is False, envelope
        assert synced['name'] == 'skills'
        assert synced['status'] == 'updated'

        meta, _ = await call_tool(
            admin_client, 'manage_skill', {'action': 'sync_meta'}
        )
        assert meta['name'] == 'skills'
        assert meta['last_ok_at'] is not None

    async def test_import_one_call_whole_skill(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        """The external-agent import: SKILL.md + text + binary +
        exec bits in ONE call, slug derived from the frontmatter
        name, files byte-identical on disk."""
        png = base64.b64decode(PNG_B64)
        imported, envelope = await call_tool(
            admin_client,
            'manage_skill',
            {
                'action': 'import',
                'skill_md': (
                    '---\nname: listing-add\ndescription: drafts\n---\n'
                    '# listing-add\n'
                ),
                'files': {
                    'references/n.md': '# n',
                    'scripts/run.sh': '#!/bin/sh\n',
                },
                'files_bin': {'assets/template.png': PNG_B64},
                'executables': ['scripts/run.sh'],
                'origin_url': 'local skill dir',
            },
        )
        assert envelope['isError'] is False, envelope
        assert imported == {
            'slug': 'listing-add',
            'path': '.claude/skills/listing-add',
            'action': 'created',
        }
        skill_dir = mock_workspace.root / '.claude' / 'skills' / 'listing-add'
        assert (skill_dir / 'references' / 'n.md').read_text() == '# n'
        assert (skill_dir / 'assets' / 'template.png').read_bytes() == png
        assert os.stat(skill_dir / 'scripts' / 'run.sh').st_mode & 0o111, (
            'executables must land with the executable bit'
        )

    async def test_import_refuses_traversal(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        _, envelope = await call_tool(
            admin_client,
            'manage_skill',
            {
                'action': 'import',
                'skill_md': '---\nname: x\ndescription: d\n---\n',
                'files_bin': {'../escape.png': PNG_B64},
            },
        )
        assert envelope['isError'] is True
        assert 'file path' in envelope['content'][0]['text'].lower()
        assert not (
            mock_workspace.root / '.claude' / 'skills' / 'x'
        ).exists(), 'a refused import writes nothing'

    async def test_import_over_an_existing_skill_needs_overwrite(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        """The wipe checkpoint: an import never destroys a skill the
        user already has unless the caller says overwrite=true."""
        first, envelope = await call_tool(
            admin_client,
            'manage_skill',
            {
                'action': 'import',
                'skill_md': '---\nname: mine\ndescription: d\n---\n# v1\n',
                'files': {'hand-made.md': 'work'},
            },
        )
        assert envelope['isError'] is False, envelope

        refused, ref_env = await call_tool(
            admin_client,
            'manage_skill',
            {
                'action': 'import',
                'skill_md': '---\nname: mine\ndescription: d\n---\n# v2\n',
                'files': {'new.md': 'y'},
            },
        )
        assert ref_env['isError'] is True, refused
        message = ref_env['content'][0]['text'].lower()
        assert 'already exists' in message
        assert 'overwrite=true' in message
        skill_dir = mock_workspace.root / '.claude' / 'skills' / 'mine'
        assert (skill_dir / 'hand-made.md').read_text() == 'work'
        assert (skill_dir / 'SKILL.md').read_text().endswith('# v1\n')

        allowed, ok_env = await call_tool(
            admin_client,
            'manage_skill',
            {
                'action': 'import',
                'skill_md': '---\nname: mine\ndescription: d\n---\n# v2\n',
                'files': {'new.md': 'y'},
                'overwrite': True,
            },
        )
        assert ok_env['isError'] is False, allowed
        assert not (skill_dir / 'hand-made.md').exists()
        assert (skill_dir / 'new.md').read_text() == 'y'
