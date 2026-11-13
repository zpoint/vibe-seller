"""Workflow-level failing tests from the worker-S import review.

Same stack as ``test_wf_agent_mcp_upload.py`` — real HTTP routes, real
WorkspaceManager — pinning the four user-visible gaps there (see
~/vibe-seller-docs/report-s.md):

1. POST /skill on an existing slug returns 200 and destroys the
   user's SKILL.md (the import flow's ``overwrite=true`` gate has a
   second, unguarded door);
2. POST /skill with a name that slugifies to the empty string claims
   success and plants a stray file at ``skills/SKILL.md``;
3. DELETE /skills/{slug} removes a BUILT-IN (synced) skill — the
   read-only contract is enforced for writes and the ``_`` prefix
   only;
4. ``manage_skill(action=delete)`` hands the same deletion to any
   external agent holding an API token, and ``manage_skill(create)``
   the same gate bypass.

These tests are RED by design: they are the acceptance bar for the
import work landing on top of these routes.
"""

import io
import json
from pathlib import Path
import tarfile

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from app import skill_uploads
from app.auth import create_token
from app.main import app
from app.workspace.manager import WorkspaceManager

pytestmark = pytest.mark.workflow

MCP_PATH = '/api/mcp'

SKILL_MD = '---\nname: gate-gap\ndescription: review gap\n---\n# gate-gap\n'
HANDWRITTEN = (
    '---\nname: gate-gap\ndescription: hand made\n---\n'
    '# gate-gap\n\nThe user wrote this.\n'
)


@pytest.fixture
def real_workspace(monkeypatch, tmp_path):
    """The REAL workspace manager on a tmp root (routes + MCP hit it)."""
    root = tmp_path / 'ws'
    (root / '.claude' / 'skills').mkdir(parents=True)
    (root / '.git').mkdir()
    manager = WorkspaceManager(root)
    monkeypatch.setattr('app.routers.workspace.workspace_manager', manager)
    monkeypatch.setattr(
        'app.agent_mcp.tools_workspace.workspace_manager', manager
    )
    return manager


@pytest.fixture
def upload_staging(monkeypatch, tmp_path):
    monkeypatch.setattr(
        skill_uploads, '_uploads_dir', lambda: tmp_path / 'uploads'
    )
    return tmp_path / 'uploads'


@pytest_asyncio.fixture
async def gap_client(
    override_async_session,
    admin_user,
    install_fake_agent,
    mock_browser_wf,
    mock_knowledge_sync,
    mock_skills_sync,
    real_workspace,
    isolated_profiles,
    fast_polling,
):
    token = create_token(admin_user.id, 'admin')
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url='http://test'
    ) as client:
        client.cookies.set('auth_token', token)
        yield client


def _builtin_dir(real_workspace, slug: str) -> Path:
    """Put a maintainer-synced built-in on disk, honestly marked."""
    skills_dir = real_workspace.root / '.claude' / 'skills'
    (skills_dir / '.sync_meta.json').write_text(
        json.dumps({'synced_skills': [slug]})
    )
    d = skills_dir / slug
    d.mkdir()
    (d / 'SKILL.md').write_text(
        f'---\nname: {slug}\ndescription: builtin\n---\n'
        'REAL CLI REFERENCE — do not clobber\n'
    )
    return d


class TestCreateRouteDestroys:
    async def test_second_create_on_same_slug_is_refused(self, gap_client):
        r = await gap_client.post(
            '/api/workspace/skill',
            json={'name': 'gap-skill', 'description': 'first'},
        )
        assert r.status_code == 200, r.text
        # The user hand-edits the SKILL.md through the normal save path.
        r = await gap_client.put(
            '/api/workspace/skills/gap-skill', json={'skill_md': HANDWRITTEN}
        )
        assert r.status_code == 200, r.text

        r = await gap_client.post(
            '/api/workspace/skill', json={'name': 'Gap Skill'}
        )
        assert r.status_code == 400, (
            'create on an existing slug must REFUSE (it silently '
            f'overwrote SKILL.md instead): got {r.status_code} {r.text}'
        )

    async def test_nonascii_name_is_a_400_not_a_stray_file(
        self, gap_client, real_workspace
    ):
        r = await gap_client.post(
            '/api/workspace/skill',
            json={'name': '中文技能', 'description': 'chinese name'},
        )
        assert r.status_code == 400, r.text
        skills_dir = real_workspace.root / '.claude' / 'skills'
        assert not (skills_dir / 'SKILL.md').exists(), (
            'an empty slug wrote straight into the skills root'
        )


class TestDeleteRouteEatsBuiltins:
    async def test_delete_builtin_is_refused(self, gap_client, real_workspace):
        d = _builtin_dir(real_workspace, 'gap-builtin')
        r = await gap_client.delete('/api/workspace/skills/gap-builtin')
        assert r.status_code == 400, (
            'built-ins are declared read-only, yet DELETE accepted '
            f'it: {r.status_code} {r.text}'
        )
        assert d.is_dir(), 'the built-in tree was removed from the workspace'


class TestUploadRouteArgumentParsing:
    async def test_unparseable_exp_is_a_403_not_an_exception(
        self, gap_client, real_workspace
    ):
        """``int(request.query_params.get('exp') or 0)`` in
        ``put_skill_archive`` runs BEFORE the signature check — a
        non-digit ``exp`` raises ValueError out of the route (500 on
        a real server, an propagated exception under the ASGI test
        transport). The endpoint is unauthenticated by design (the
        signature is the auth), so ANY request can trigger it. A
        malformed signature input must answer like the signature
        gate does: refuse, don't crash."""
        r = await gap_client.put(
            '/api/skill-uploads/0123456789abcdef0123456789abcdef'
            '?uid=x&exp=abc&sig=y',
            content=b'x',
            headers={'Content-Type': 'application/gzip'},
        )
        assert r.status_code in (403, 404), r.text


class TestMcpManageSkillSurfaced:
    """The same two holes through the external agent toolset — this is
    what a ``vst_`` token can do, tool text promising 'built-in skills
    are read-only'."""

    async def _tool(self, client, arguments):
        r = await client.post(
            MCP_PATH,
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {
                    'name': 'manage_skill',
                    'arguments': arguments,
                },
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        result = body['result']
        text = result['content'][0]['text']
        is_error = bool(result.get('isError'))
        if is_error:
            return {'error': text}, True
        return (json.loads(text) if text else {}), False

    async def test_manage_skill_delete_refuses_builtin(
        self, gap_client, real_workspace
    ):
        d = _builtin_dir(real_workspace, 'gap-builtin')
        payload, is_error = await self._tool(
            gap_client, {'action': 'delete', 'slug': 'gap-builtin'}
        )
        assert is_error, (
            f'manage_skill(delete) destroyed a read-only built-in: {payload}'
        )
        assert d.is_dir()

    async def test_manage_skill_create_bypasses_the_overwrite_gate(
        self, gap_client, real_workspace, upload_staging
    ):
        """The documented contract: wholesale install over an existing
        user skill is refused until the USER confirms (overwrite=true).
        This test proves the refusal is bypassable through create: the
        archive import is refused, then one create call blanks the
        user's SKILL.md with the scaffold template — no confirmation,
        no refusal."""
        # 1. A user skill, hand-written.
        r = await gap_client.put(
            '/api/workspace/skills/gate-gap', json={'skill_md': HANDWRITTEN}
        )
        assert r.status_code == 200, r.text

        # 2. An archive import over it: refused without overwrite=true.
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as tar:
            payload = SKILL_MD.encode()
            info = tarfile.TarInfo('gate-gap/SKILL.md')
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        minted, err = await self._tool(gap_client, {'action': 'begin_upload'})
        assert not err, minted
        put_url = minted['put_url']
        parsed_path = put_url[put_url.index('/api/') :]
        up = await gap_client.put(
            parsed_path,
            content=buf.getvalue(),
            headers={'Content-Type': 'application/gzip'},
        )
        assert up.status_code == 200, up.text
        refused, err = await self._tool(
            gap_client,
            {'action': 'import', 'upload_id': minted['upload_id']},
        )
        assert err and 'overwrite=true' in json.dumps(refused), refused

        # 3. The gate door: create under the same name.
        result, error = await self._tool(
            gap_client,
            {'action': 'create', 'name': 'gate-gap', 'description': 'x'},
        )
        assert error, (
            'create silently replaced an existing skill through the '
            f'unguarded door: {result}'
        )
        skill_md = (
            real_workspace.root / '.claude' / 'skills' / 'gate-gap' / 'SKILL.md'
        ).read_text()
        assert 'The user wrote this.' in skill_md
