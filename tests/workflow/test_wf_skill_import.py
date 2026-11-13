"""Workflow: a task installs a skill — local DIRECTORY and URL —
through the REAL HTTP stack and the REAL workspace manager.

Mirrors ``test_wf_agent_mcp_upload.py`` (the external archive flow)
for the two new sources:

* in-task ``vibe_seller_import_skill(path=…)`` — the MCP process
  packs the directory (``app.skill_pack``) and POSTs the archive to
  ``/api/workspace/skills/import-local``; files land byte-for-byte
  (binary file + exec bit), macOS junk is skipped and reported;
* in-task ``vibe_seller_import_skill(url=…)`` and external
  ``manage_skill(action=import, url=…)`` — the SERVER fetches via
  ``app.skill_fetch`` and installs through the same staged path.
  A loopback fixture serves a direct archive AND a fake GitHub
  trees/blobs layout (no real network in tests).

Refusals pinned here: no SKILL.md, symlink escaping the directory,
built-in slug, existing user skill without overwrite, oversize,
non-http scheme, private address (even with the loopback flag), and
loopback without the flag.
"""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import threading
import unicodedata

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from app import mcp_server, skill_uploads
from app.auth import create_token
from app.main import app
from app.routers import workspace as workspace_route
from app.workspace.manager import WorkspaceManager

pytestmark = pytest.mark.workflow

PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
    b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)
BIN = bytes(range(256)) * 64  # 16 KB of non-text
RUN = b'#!/bin/sh\necho invoice-check\n'


def _skill_md(name: str) -> str:
    return f'---\nname: {name}\ndescription: wf import\n---\n# {name}\n'


def _build_skill_dir(tmp_path, name: str = 'wf-import-skill'):
    """A source dir: SKILL.md + nested text + BINARY + exec script +
    macOS junk + an internal symlink (must dereference)."""
    d = tmp_path / f'src-{name}'
    (d / 'assets').mkdir(parents=True)
    (d / 'scripts').mkdir()
    (d / 'SKILL.md').write_text(_skill_md(name), encoding='utf-8')
    (d / 'assets' / 'icon.png').write_bytes(PNG)
    (d / 'assets' / 'model.bin').write_bytes(BIN)
    run = d / 'scripts' / 'run.sh'
    run.write_bytes(RUN)
    run.chmod(0o755)
    notes = d / 'notes.md'
    notes.write_text('top-level text\n', encoding='utf-8')
    (d / '.DS_Store').write_bytes(b'junk')
    (d / 'assets' / '._icon.png').write_bytes(b'junk')
    (d / 'notes-link.md').symlink_to(notes)  # inside the dir: allowed
    return d


def _assert_installed(skill_dir, name='wf-import-skill'):
    assert (skill_dir / 'SKILL.md').read_text() == _skill_md(name)
    assert (skill_dir / 'assets' / 'icon.png').read_bytes() == PNG
    assert (skill_dir / 'assets' / 'model.bin').read_bytes() == BIN
    assert (skill_dir / 'notes.md').read_text() == 'top-level text\n'
    # dereferenced in-dir symlink: a plain copy of the target bytes
    assert (skill_dir / 'notes-link.md').read_bytes() == b'top-level text\n'
    assert not (skill_dir / 'notes-link.md').is_symlink()
    assert (skill_dir / 'scripts' / 'run.sh').stat().st_mode & 0o111
    # junk skipped by extraction, not installed
    assert not (skill_dir / '.DS_Store').exists()
    assert not (skill_dir / 'assets' / '._icon.png').exists()


# ── Fixtures: real workspace, staged uploads, MCP call routing ──


@pytest.fixture
def real_workspace(monkeypatch, tmp_path):
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
async def client(
    override_async_session,
    admin_user,
    install_fake_agent,
    mock_browser_wf,
    mock_knowledge_sync,
    mock_skills_sync,
    real_workspace,
    upload_staging,
    isolated_profiles,
    fast_polling,
):
    token = create_token(admin_user.id, 'admin')
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url='http://test'
    ) as aclient:
        aclient.cookies.set('auth_token', token)
        yield aclient


def _route_mcp_call_api(client, monkeypatch) -> None:
    """Send the in-task MCP server's API calls through the real app,
    JSON bodies AND raw bytes (the packed archive) alike — the same
    shape production ``call_api`` has."""

    async def _call_api(method, path, body=None, timeout=60, **kwargs):
        content = kwargs.get('content')
        headers = (
            {'Content-Type': kwargs.get('content_type', 'application/gzip')}
            if content is not None
            else {}
        )
        resp = await client.request(
            method, path, content=content, json=body, headers=headers
        )
        return resp.json()

    monkeypatch.setattr('app.mcp_server.call_api', _call_api)


async def _import_tool(arguments) -> dict:
    """What the task agent's tool call comes back as (JSON string)."""
    return json.loads(
        await mcp_server.handle_tool_call('vibe_seller_import_skill', arguments)
    )


# ── Loopback source server (archive + fake GitHub API) ─────────


@pytest.fixture
def source_server():
    """A 127.0.0.1 fixture with a routes dict the tests fill in.

    Values: ``('bytes', payload)`` | ``('json', obj)`` |
    ``('redirect', target_path)`` — keyed by path (query ignored).
    """
    routes: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server API
            key = self.path.split('?')[0]
            route = routes.get(key)
            if route is None:
                self.send_response(404)
                self.end_headers()
                return
            kind = route[0]
            if kind == 'redirect':
                self.send_response(302)
                self.send_header('Location', route[1])
                self.end_headers()
                return
            if kind == 'json':
                payload = json.dumps(route[1]).encode()
                ctype = 'application/json'
            else:
                payload, ctype = route[1], 'application/octet-stream'
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {'routes': routes, 'base': f'http://127.0.0.1:{port}'}
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def loopback_ok(monkeypatch):
    """The same test switch the webhook stubs use, honoured by
    app.skill_fetch (read at call time)."""
    monkeypatch.setenv('VIBE_ALLOW_LOOPBACK_WEBHOOKS', '1')


def _tar_gz(entries: dict[str, bytes]) -> bytes:
    import tarfile
    import time

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for name, payload in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mtime = int(time.time())
            info.mode = 0o755 if name.endswith('.sh') else 0o644
            tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


class TestLocalDirectoryImport:
    """The in-task tool with ``path``: pack here, install there."""

    async def test_round_trip_byte_identical(
        self, client, real_workspace, monkeypatch, tmp_path
    ):
        _route_mcp_call_api(client, monkeypatch)
        d = _build_skill_dir(tmp_path)
        result = await _import_tool({'path': str(d)})
        assert result['slug'] == 'wf-import-skill', result
        assert result['action'] == 'created'
        assert sorted(result['ignored']) == [
            '.DS_Store',
            'assets/._icon.png',
        ]
        skill_dir = (
            real_workspace.root / '.claude' / 'skills' / 'wf-import-skill'
        )
        _assert_installed(skill_dir)
        # provenance: the path lands in the lockfile → lists imported
        entry = real_workspace._read_lockfile()['skills']['wf-import-skill']
        assert entry['source'] == 'url'
        assert entry['origin_url'] == str(d)

    async def test_overwrite_true_replaces(
        self, client, real_workspace, monkeypatch, tmp_path
    ):
        _route_mcp_call_api(client, monkeypatch)
        d = _build_skill_dir(tmp_path)
        await _import_tool({'path': str(d)})
        # a second source dir carrying the SAME skill name: same slug
        d2 = tmp_path / 'src-second'
        d2.mkdir()
        (d2 / 'SKILL.md').write_text(_skill_md('wf-import-skill'))
        (d2 / 'notes.md').write_text('changed\n')
        again = await _import_tool({'path': str(d2), 'overwrite': True})
        assert again['action'] == 'updated', again
        skill_dir = (
            real_workspace.root / '.claude' / 'skills' / 'wf-import-skill'
        )
        assert (skill_dir / 'notes.md').read_text() == 'changed\n'
        # replace semantics: files the new tree does not carry are gone
        assert not (skill_dir / 'scripts').exists()

    async def test_no_skill_md_refused_before_the_wire(
        self, client, real_workspace, monkeypatch, tmp_path
    ):
        _route_mcp_call_api(client, monkeypatch)
        bare = tmp_path / 'bare'
        bare.mkdir()
        result = await _import_tool({'path': str(bare)})
        assert 'SKILL.md at its root' in result['error'], result

    async def test_symlink_escape_refused(
        self, client, real_workspace, monkeypatch, tmp_path
    ):
        _route_mcp_call_api(client, monkeypatch)
        d = _build_skill_dir(tmp_path)
        (d / 'out.md').symlink_to(tmp_path / 'elsewhere.md')
        (tmp_path / 'elsewhere.md').write_text('outside')
        result = await _import_tool({'path': str(d)})
        assert 'escapes the skill directory' in result['error'], result

    async def test_oversize_refused(
        self, client, real_workspace, monkeypatch, tmp_path
    ):
        import app.skill_pack as sp

        _route_mcp_call_api(client, monkeypatch)
        monkeypatch.setattr(sp, 'MAX_SKILL_TOTAL_BYTES', 10)
        d = _build_skill_dir(tmp_path)
        result = await _import_tool({'path': str(d)})
        assert 'total size limit' in result['error'], result

    async def test_builtin_slug_refused(
        self, client, real_workspace, monkeypatch, tmp_path
    ):
        _route_mcp_call_api(client, monkeypatch)
        skills = real_workspace.root / '.claude' / 'skills'
        (skills / '.sync_meta.json').write_text(
            json.dumps({'synced_skills': ['wf-import-skill']})
        )
        d = _build_skill_dir(tmp_path)
        result = await _import_tool({'path': str(d)})
        assert 'built-in' in result['detail'], result
        assert not (skills / 'wf-import-skill').exists()

    async def test_existing_skill_needs_overwrite_gate(
        self, client, real_workspace, monkeypatch, tmp_path
    ):
        _route_mcp_call_api(client, monkeypatch)
        d = _build_skill_dir(tmp_path)
        first = await _import_tool({'path': str(d)})
        assert first['action'] == 'created'
        refused = await _import_tool({'path': str(d)})
        assert 'already exists' in refused['detail'], refused
        assert 'overwrite=true' in refused['detail']

    async def test_nonascii_file_names_round_trip(
        self, client, real_workspace, monkeypatch, tmp_path
    ):
        """Legit (non-colliding) Unicode names survive pack → extract
        → install byte-identical (the normalization rule only refuses
        pairs that would collide on one host)."""
        _route_mcp_call_api(client, monkeypatch)
        d = _build_skill_dir(tmp_path)
        cafe = unicodedata.normalize('NFC', 'café-notes.md')
        (d / cafe).write_text('références\n', encoding='utf-8')
        result = await _import_tool({'path': str(d)})
        assert result['action'] == 'created', result
        skill_dir = (
            real_workspace.root / '.claude' / 'skills' / 'wf-import-skill'
        )
        assert (skill_dir / cafe).read_text(encoding='utf-8') == 'références\n'

    async def test_explicit_slug_override(
        self, client, real_workspace, monkeypatch, tmp_path
    ):
        _route_mcp_call_api(client, monkeypatch)
        d = _build_skill_dir(tmp_path)
        result = await _import_tool({'path': str(d), 'slug': 'chosen-slug'})
        assert result['slug'] == 'chosen-slug'
        _assert_installed(
            real_workspace.root / '.claude' / 'skills' / 'chosen-slug'
        )

    async def test_bad_slug_shape_refused_by_the_core(
        self, client, real_workspace, monkeypatch, tmp_path
    ):
        """A hand-passed slug with traversal must not walk the install
        out of the skills dir (the blocking-core guard)."""
        _route_mcp_call_api(client, monkeypatch)
        d = _build_skill_dir(tmp_path)
        result = await _import_tool({'path': str(d), 'slug': '../escape'})
        assert 'Invalid skill slug' in result['detail'], result
        assert not (real_workspace.root.parent / 'escape').exists()


class TestUrlImportInTask:
    async def test_direct_archive_url(
        self, client, real_workspace, monkeypatch, source_server, loopback_ok
    ):
        _route_mcp_call_api(client, monkeypatch)
        source_server['routes']['/tar/wf-skill.tar.gz'] = (
            'bytes',
            _tar_gz({
                'wf-skill/SKILL.md': _skill_md('wf-import-skill').encode(),
                'wf-skill/assets/icon.png': PNG,
                'wf-skill/scripts/run.sh': RUN,
            }),
        )
        result = await _import_tool({
            'url': f'{source_server["base"]}/tar/wf-skill.tar.gz'
        })
        assert result['slug'] == 'wf-import-skill', result
        skill_dir = (
            real_workspace.root / '.claude' / 'skills' / 'wf-import-skill'
        )
        assert (skill_dir / 'assets' / 'icon.png').read_bytes() == PNG
        assert (skill_dir / 'scripts' / 'run.sh').stat().st_mode & 0o111
        # origin_url defaults to the URL → lists as imported
        entry = real_workspace._read_lockfile()['skills']['wf-import-skill']
        assert entry['source'] == 'url'
        assert entry['origin_url'].endswith('/tar/wf-skill.tar.gz')

    async def test_github_tree_url_with_exec_bit(
        self, client, real_workspace, monkeypatch, source_server, loopback_ok
    ):
        _route_mcp_call_api(client, monkeypatch)
        md, run = _skill_md('wf-import-skill').encode(), RUN
        tree = {
            'truncated': False,
            'tree': [
                {'path': 'invoice-check', 'mode': '040000', 'type': 'tree'},
                {
                    'path': 'invoice-check/SKILL.md',
                    'mode': '100644',
                    'type': 'blob',
                    'sha': 'aaa1',
                    'size': len(md),
                },
                {
                    'path': 'invoice-check/scripts',
                    'mode': '040000',
                    'type': 'tree',
                },
                {
                    'path': 'invoice-check/scripts/run.sh',
                    'mode': '100755',
                    'type': 'blob',
                    'sha': 'bbb2',
                    'size': len(run),
                },
                {
                    'path': 'invoice-check/examples',
                    'mode': '040000',
                    'type': 'tree',
                },
                {
                    'path': 'invoice-check/examples/demo.md',
                    'mode': '100644',
                    'type': 'blob',
                    'sha': 'ccc3',
                    'size': 6,
                },
                {
                    'path': 'invoice-check/examples/icon.png',
                    'mode': '100644',
                    'type': 'blob',
                    'sha': 'ddd4',
                    'size': len(PNG),
                },
            ],
        }
        source_server['routes'][
            '/repos/acme/skills/git/trees/main:invoice-check'
        ] = ('json', tree)
        for sha, data in (
            ('aaa1', md),
            ('bbb2', run),
            ('ccc3', b'demo\n'),
            ('ddd4', PNG),
        ):
            source_server['routes'][f'/repos/acme/skills/git/blobs/{sha}'] = (
                'json',
                {
                    'encoding': 'base64',
                    'content': base64.b64encode(data).decode(),
                },
            )
        result = await _import_tool({
            'url': (
                f'{source_server["base"]}/acme/skills/tree/main/invoice-check'
            )
        })
        assert result['slug'] == 'wf-import-skill', result
        skill_dir = (
            real_workspace.root / '.claude' / 'skills' / 'wf-import-skill'
        )
        # nested directories install (round-6 bug: only top-level files
        # arrived, silently). The installed set is EXACTLY the source.
        names = sorted(
            str(p.relative_to(skill_dir))
            for p in skill_dir.rglob('*')
            if p.is_file()
        )
        assert names == [
            'SKILL.md',
            'examples/demo.md',
            'examples/icon.png',
            'scripts/run.sh',
        ], names
        assert (skill_dir / 'examples' / 'demo.md').read_bytes() == b'demo\n'
        assert (skill_dir / 'examples' / 'icon.png').read_bytes() == PNG
        assert (skill_dir / 'scripts' / 'run.sh').read_bytes() == run
        assert (skill_dir / 'scripts' / 'run.sh').stat().st_mode & 0o111

    async def test_github_api_without_recursive_fails_loudly(
        self, client, real_workspace, monkeypatch, source_server, loopback_ok
    ):
        """A GitHub-compatible server that ignored recursive=1 must
        never import a top-level-only subset — an unexpanded directory
        is a refusal, not silence (the round-6 bug class)."""
        _route_mcp_call_api(client, monkeypatch)
        md = _skill_md('wf-import-skill').encode()
        source_server['routes'][
            '/repos/acme/skills/git/trees/main:nested-skill'
        ] = (
            'json',
            {
                'truncated': False,
                'tree': [
                    {
                        'path': 'nested-skill/SKILL.md',
                        'mode': '100644',
                        'type': 'blob',
                        'sha': 'aaa1',
                    },
                    {
                        'path': 'nested-skill/examples',
                        'mode': '040000',
                        'type': 'tree',
                    },
                ],
            },
        )
        source_server['routes']['/repos/acme/skills/git/blobs/aaa1'] = (
            'json',
            {'encoding': 'base64', 'content': base64.b64encode(md).decode()},
        )
        result = await _import_tool({
            'url': (
                f'{source_server["base"]}/acme/skills/tree/main/nested-skill'
            )
        })
        assert 'did not expand' in result['detail'], result

    async def test_redirect_revalidated_to_loopback(
        self, client, real_workspace, monkeypatch, source_server, loopback_ok
    ):
        _route_mcp_call_api(client, monkeypatch)
        payload = _tar_gz({'x/SKILL.md': _skill_md('wf-import-skill').encode()})
        source_server['routes']['/tar/final.tar.gz'] = ('bytes', payload)
        source_server['routes']['/tar/r'] = (
            'redirect',
            '/tar/final.tar.gz',
        )
        result = await _import_tool({'url': f'{source_server["base"]}/tar/r'})
        assert result['slug'] == 'wf-import-skill', result

    async def test_loopback_without_the_flag_refused(
        self, client, real_workspace, monkeypatch, source_server
    ):
        monkeypatch.delenv('VIBE_ALLOW_LOOPBACK_WEBHOOKS', raising=False)
        _route_mcp_call_api(client, monkeypatch)
        result = await _import_tool({
            'url': f'{source_server["base"]}/tar/anything.tar.gz'
        })
        assert 'not a public address' in result['detail'], result

    async def test_private_address_refused_even_with_the_flag(
        self, client, real_workspace, monkeypatch, loopback_ok
    ):
        _route_mcp_call_api(client, monkeypatch)
        result = await _import_tool({'url': 'http://10.0.0.7/skill.tar.gz'})
        assert 'not a public address' in result['detail'], result

    async def test_non_http_scheme_refused(
        self, client, real_workspace, monkeypatch, loopback_ok
    ):
        _route_mcp_call_api(client, monkeypatch)
        result = await _import_tool({'url': 'file:///tmp/skill.tar.gz'})
        assert 'only http(s)' in result['detail'], result

    async def test_archive_without_skill_md_refused(
        self, client, real_workspace, monkeypatch, source_server, loopback_ok
    ):
        _route_mcp_call_api(client, monkeypatch)
        source_server['routes']['/tar/no-md.tar.gz'] = (
            'bytes',
            _tar_gz({'x/readme.md': b'no skill here'}),
        )
        result = await _import_tool({
            'url': f'{source_server["base"]}/tar/no-md.tar.gz'
        })
        assert 'no SKILL.md at its root' in result['detail'], result

    async def test_oversize_archive_refused(
        self,
        client,
        real_workspace,
        monkeypatch,
        source_server,
        loopback_ok,
    ):
        import os

        import app.skill_fetch as sf

        _route_mcp_call_api(client, monkeypatch)
        monkeypatch.setattr(sf, 'MAX_ARCHIVE_UPLOAD_BYTES', 64)
        source_server['routes']['/tar/big.tar.gz'] = (
            'bytes',
            _tar_gz({
                'x/SKILL.md': _skill_md('wf-import-skill').encode(),
                'x/rand.bin': os.urandom(2048),
            }),
        )
        result = await _import_tool({
            'url': f'{source_server["base"]}/tar/big.tar.gz'
        })
        assert 'archive limit' in result['detail'], result

    async def test_unreachable_url_is_a_clean_refusal(
        self, client, real_workspace, monkeypatch, loopback_ok
    ):
        """A connect-level failure must answer as an actionable
        refusal. Observed: a raw httpx.ConnectError escaped the route
        as 500 Internal Server Error, leaving the agent nothing to
        act on."""
        import socket

        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            dead_port = s.getsockname()[1]
        _route_mcp_call_api(client, monkeypatch)
        result = await _import_tool({
            'url': f'http://127.0.0.1:{dead_port}/skill.tar.gz'
        })
        assert 'could not reach' in result['detail'], result

    async def test_github_symlink_entry_refused(
        self, client, real_workspace, monkeypatch, source_server, loopback_ok
    ):
        _route_mcp_call_api(client, monkeypatch)
        source_server['routes'][
            '/repos/acme/skills/git/trees/main:evil-skill'
        ] = (
            'json',
            {
                'truncated': False,
                'tree': [
                    {
                        'path': 'evil-skill/SKILL.md',
                        'mode': '100644',
                        'type': 'blob',
                        'sha': 'aaa1',
                    },
                    {
                        'path': 'evil-skill/link',
                        'mode': '120000',
                        'type': 'link',
                        'sha': 'ccc3',
                    },
                ],
            },
        )
        result = await _import_tool({
            'url': f'{source_server["base"]}/acme/skills/tree/main/evil-skill'
        })
        assert 'symlink' in result['detail'], result


MCP_PATH = '/api/mcp'


async def _mcp_call(client, name, arguments):
    r = await client.post(
        MCP_PATH,
        json={
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert 'error' not in body, body
    text = body['result']['content'][0]['text']
    if body['result'].get('isError'):
        return {'error': text}
    return json.loads(text)


class TestExternalManageSkillUrl:
    """manage_skill(action=import, url=…) — one install path for the
    external agent toolset too."""

    async def test_url_import_and_overwrite_gate(
        self, client, real_workspace, source_server, loopback_ok
    ):
        payload = _tar_gz({
            'x/SKILL.md': _skill_md('wf-import-skill').encode(),
            'x/scripts/run.sh': RUN,
        })
        source_server['routes']['/tar/ext.tar.gz'] = ('bytes', payload)
        url = f'{source_server["base"]}/tar/ext.tar.gz'
        imported = await _mcp_call(
            client, 'manage_skill', {'action': 'import', 'url': url}
        )
        assert imported.get('slug') == 'wf-import-skill', imported
        skill_dir = (
            real_workspace.root / '.claude' / 'skills' / 'wf-import-skill'
        )
        assert (skill_dir / 'scripts' / 'run.sh').read_bytes() == RUN
        assert (skill_dir / 'scripts' / 'run.sh').stat().st_mode & 0o111

        refused = await _mcp_call(
            client, 'manage_skill', {'action': 'import', 'url': url}
        )
        assert 'already exists' in refused['error'], refused
        replaced = await _mcp_call(
            client,
            'manage_skill',
            {'action': 'import', 'url': url, 'overwrite': True},
        )
        assert replaced.get('action') == 'updated', replaced

    async def test_upload_and_url_are_mutually_exclusive(
        self, client, real_workspace
    ):
        refused = await _mcp_call(
            client,
            'manage_skill',
            {
                'action': 'import',
                'upload_id': 'a' * 32,
                'url': 'https://example.invalid/x.tar.gz',
            },
        )
        assert 'upload_id OR url' in refused['error'], refused


class TestRouteErrorMapping:
    def test_fetch_error_status_survives_the_hop(self):
        from app.skill_fetch import FetchError

        exc = FetchError('over the limit', 413)
        http = workspace_route._import_http_exception(exc)
        assert isinstance(http, HTTPException)
        assert http.status_code == 413


class TestUploadIdSingleUse:
    """The consumption contract every installer depends on (coverage
    gap #7 of the S review — two concurrent installers must not turn
    one upload into two installs)."""

    async def test_importing_twice_with_one_upload_id_is_refused(
        self, client, real_workspace
    ):
        payload = _tar_gz({'x/SKILL.md': _skill_md('wf-import-skill').encode()})
        minted = await _mcp_call(
            client, 'manage_skill', {'action': 'begin_upload'}
        )
        assert 'upload_id' in minted, minted
        parsed = minted['put_url'][minted['put_url'].index('/api/') :]
        put = await client.put(
            parsed,
            content=payload,
            headers={'Content-Type': 'application/gzip'},
        )
        assert put.status_code == 200, put.text
        first = await _mcp_call(
            client,
            'manage_skill',
            {'action': 'import', 'upload_id': minted['upload_id']},
        )
        assert first.get('slug') == 'wf-import-skill', first
        second = await _mcp_call(
            client,
            'manage_skill',
            {'action': 'import', 'upload_id': minted['upload_id']},
        )
        assert 'already consumed' in second['error'], second
