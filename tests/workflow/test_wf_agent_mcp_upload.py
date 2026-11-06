"""Workflow tests for the presigned skill-archive upload flow.

The full journey through the REAL HTTP stack and the REAL workspace
manager (no conftest fake — the point is to cover the real code):

``/api/mcp`` ``manage_skill(begin_upload)`` -> raw ``PUT`` of an
archive to the returned ``put_url`` -> ``manage_skill(import,
upload_id)`` -> ``manage_skill(get)`` + byte-for-byte compare with
the source tree and its executable bits.

Plus the refusals: expired URL, reused URL, oversize body, path
traversal archive, symlink archive, and the overwrite gate.
"""

import io
import json
from pathlib import Path
import tarfile
import time
from urllib.parse import urlparse
import zipfile

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from app import skill_uploads
from app.auth import create_token
from app.main import app
from app.workspace.manager import WorkspaceManager

pytestmark = pytest.mark.workflow

MCP_PATH = '/api/mcp'

PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
    b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)
BINARY = bytes(range(256)) * 64  # 16 KB of non-text
SKILL_MD = (
    '---\nname: upload-skill\ndescription: from an archive\n---\n'
    '# upload-skill\n\nBody.\n'
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
def real_workspace(monkeypatch, tmp_path):
    """The REAL workspace manager — the upload flow must install
    through it, not the conftest fake."""
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
async def upload_client(
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
    ) as client:
        client.cookies.set('auth_token', token)
        yield client


def _archive_bytes(entries, kind='tar.gz'):
    buf = io.BytesIO()
    if kind == 'zip':
        with zipfile.ZipFile(buf, 'w') as zf:
            for name, payload in entries.items():
                info = zipfile.ZipInfo(name)
                info.external_attr = (
                    0o755 if name.endswith('run.py') else 0o644
                ) << 16
                zf.writestr(info, payload)
        return buf.getvalue()
    mode = 'w:gz' if kind == 'tar.gz' else 'w'
    with tarfile.open(fileobj=buf, mode=mode) as tar:
        for name, payload in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755 if name.endswith('run.py') else 0o644
            tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def _good_entries():
    return {
        'skill/SKILL.md': SKILL_MD.encode(),
        'skill/assets/template.png': PNG,
        'skill/assets/model.bin': BINARY,
        'skill/scripts/run.py': b'print("hi")\n',
    }


async def _begin(client):
    result, envelope = await call_tool(
        client, 'manage_skill', {'action': 'begin_upload'}
    )
    assert envelope['isError'] is False, result
    return result


def _put_target(put_url):
    parsed = urlparse(put_url)
    return f'{parsed.path}?{parsed.query}'


async def _upload(client, put_url, data, content_type='application/gzip'):
    return await client.put(
        _put_target(put_url),
        content=data,
        headers={'Content-Type': content_type},
    )


class TestFullJourney:
    async def test_tar_gz_round_trip(self, upload_client, real_workspace):
        minted = await _begin(upload_client)
        assert set(minted) >= {
            'upload_id',
            'put_url',
            'expires_at',
            'max_bytes',
            'formats',
            'curl',
        }
        put = await _upload(
            upload_client, minted['put_url'], _archive_bytes(_good_entries())
        )
        assert put.status_code == 200, put.text
        assert put.json()['format'] == 'tar.gz'

        imported, envelope = await call_tool(
            upload_client,
            'manage_skill',
            {
                'action': 'import',
                'upload_id': minted['upload_id'],
                'origin_url': 'local skill dir',
            },
        )
        assert envelope['isError'] is False, imported
        assert imported['slug'] == 'upload-skill'

        got, _ = await call_tool(
            upload_client,
            'manage_skill',
            {'action': 'get', 'slug': 'upload-skill'},
        )
        assert got['source'] == 'imported'
        assert got['files']

        skill_dir = real_workspace.root / '.claude' / 'skills' / 'upload-skill'
        assert (skill_dir / 'SKILL.md').read_bytes() == SKILL_MD.encode()
        assert (skill_dir / 'assets' / 'template.png').read_bytes() == PNG
        assert (skill_dir / 'assets' / 'model.bin').read_bytes() == BINARY
        assert os_stat_exec(skill_dir / 'scripts' / 'run.py')
        assert not (skill_dir / 'skill').exists(), 'top dir must be stripped'

    async def test_zip_and_multipart_variant(
        self, upload_client, real_workspace
    ):
        minted = await _begin(upload_client)
        data = _archive_bytes(_good_entries(), 'zip')
        parsed = urlparse(minted['put_url'])
        response = await upload_client.post(
            f'{parsed.path}?{parsed.query}',
            files={'file': ('skill.zip', data, 'application/zip')},
        )
        assert response.status_code == 200, response.text
        assert response.json()['format'] == 'zip'

        imported, envelope = await call_tool(
            upload_client,
            'manage_skill',
            {'action': 'import', 'upload_id': minted['upload_id']},
        )
        assert envelope['isError'] is False, imported
        skill_dir = real_workspace.root / '.claude' / 'skills' / 'upload-skill'
        assert (skill_dir / 'assets' / 'model.bin').read_bytes() == BINARY
        assert os_stat_exec(skill_dir / 'scripts' / 'run.py')


def os_stat_exec(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o111)


class TestRefusals:
    async def test_expired_url_403(self, upload_client, admin_user):
        minted = await _begin(upload_client)
        upload_id = minted['upload_id']
        user_id = str(admin_user.id)
        past = int(time.time()) - 5
        sig = skill_uploads._signature(upload_id, user_id, past)
        put = await upload_client.put(
            f'/api/skill-uploads/{upload_id}'
            f'?uid={user_id}&exp={past}&sig={sig}',
            content=_archive_bytes(_good_entries()),
            headers={'Content-Type': 'application/gzip'},
        )
        assert put.status_code == 403
        assert 'expired' in put.text

    async def test_reused_url_403(self, upload_client):
        minted = await _begin(upload_client)
        data = _archive_bytes(_good_entries())
        first = await _upload(upload_client, minted['put_url'], data)
        assert first.status_code == 200
        second = await _upload(upload_client, minted['put_url'], data)
        assert second.status_code == 403

    async def test_oversize_body_413(self, upload_client, monkeypatch):
        monkeypatch.setattr(skill_uploads, 'MAX_ARCHIVE_UPLOAD_BYTES', 64)
        minted = await _begin(upload_client)
        put = await _upload(
            upload_client, minted['put_url'], _archive_bytes(_good_entries())
        )
        assert put.status_code == 413

    async def test_wrong_content_type_415(self, upload_client):
        minted = await _begin(upload_client)
        put = await _upload(
            upload_client,
            minted['put_url'],
            _archive_bytes(_good_entries()),
            content_type='text/html',
        )
        assert put.status_code == 415

    async def _import_refused(self, upload_client, entries, expected):
        minted = await _begin(upload_client)
        put = await _upload(
            upload_client, minted['put_url'], _archive_bytes(entries, 'tar')
        )
        assert put.status_code == 200, put.text
        imported, envelope = await call_tool(
            upload_client,
            'manage_skill',
            {'action': 'import', 'upload_id': minted['upload_id']},
        )
        assert envelope['isError'] is True, imported
        assert expected in imported['error'].lower()

    async def test_path_traversal_archive_refused(self, upload_client):
        entries = dict(_good_entries())
        entries['skill/../escape.md'] = b'x'
        await self._import_refused(upload_client, entries, 'escapes')

    async def test_symlink_archive_refused(self, upload_client):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as tar:
            for name, payload in _good_entries().items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            link = tarfile.TarInfo('skill/evil')
            link.type = tarfile.SYMTYPE
            link.linkname = '/etc/passwd'
            tar.addfile(link)
        minted = await _begin(upload_client)
        put = await _upload(
            upload_client,
            minted['put_url'],
            buf.getvalue(),
            content_type='application/x-tar',
        )
        assert put.status_code == 200, put.text
        imported, envelope = await call_tool(
            upload_client,
            'manage_skill',
            {'action': 'import', 'upload_id': minted['upload_id']},
        )
        assert envelope['isError'] is True
        assert 'unsupported' in imported['error'].lower()

    async def _import_archive(self, upload_client, **extra):
        minted = await _begin(upload_client)
        put = await _upload(
            upload_client,
            minted['put_url'],
            _archive_bytes(_good_entries()),
        )
        assert put.status_code == 200, put.text
        return await call_tool(
            upload_client,
            'manage_skill',
            {'action': 'import', 'upload_id': minted['upload_id'], **extra},
        )

    async def test_overwrite_refusal_then_success(self, upload_client):
        imported, envelope = await self._import_archive(upload_client)
        assert envelope['isError'] is False, imported

        refused, envelope = await self._import_archive(upload_client)
        assert envelope['isError'] is True
        assert 'already exists' in refused['error']
        assert 'overwrite=true' in refused['error']

        replaced, envelope = await self._import_archive(
            upload_client, overwrite=True
        )
        assert envelope['isError'] is False, replaced


class TestInlineStillWorks:
    async def test_tiny_text_import_is_unchanged(self, upload_client):
        imported, envelope = await call_tool(
            upload_client,
            'manage_skill',
            {
                'action': 'import',
                'skill_md': ('---\nname: tiny\ndescription: d\n---\n# tiny\n'),
                'files': {'notes.md': 'hi'},
            },
        )
        assert envelope['isError'] is False, imported
        assert imported['slug'] == 'tiny'


class TestUploadIdIsNotAPath:
    async def test_traversal_upload_id_is_404(self, upload_client):
        # A path-shaped id is normalised away by the HTTP client
        # before it is sent (404/405 either way); the handler-level
        # shape check is pinned in tests/unit/test_skill_uploads.py.
        for bad in ('../../etc', 'nope', 'z' * 32):
            response = await upload_client.put(
                f'/api/skill-uploads/{bad}?uid=x&exp=1&sig=x',
                content=b'x',
                headers={'Content-Type': 'application/gzip'},
            )
            assert response.status_code in (404, 405), response.text


class TestMacosJunk:
    async def test_junk_skipped_and_reported(
        self, upload_client, real_workspace
    ):
        """macOS packing junk is dropped at extraction and REPORTED —
        the import result lists it under `ignored`, and none of it
        reaches the installed tree."""
        entries = dict(_good_entries())
        entries['skill/._template.png'] = b'junk'
        entries['__MACOSX/skill/._SKILL.md'] = b'junk'
        entries['skill/.DS_Store'] = b'junk'

        minted = await _begin(upload_client)
        put = await _upload(
            upload_client,
            minted['put_url'],
            _archive_bytes(entries, 'tar'),
            content_type='application/x-tar',
        )
        assert put.status_code == 200, put.text
        imported, envelope = await call_tool(
            upload_client,
            'manage_skill',
            {'action': 'import', 'upload_id': minted['upload_id']},
        )
        assert envelope['isError'] is False, imported
        assert sorted(imported['ignored']) == [
            '__MACOSX/skill/._SKILL.md',
            'skill/.DS_Store',
            'skill/._template.png',
        ], imported

        skill_dir = real_workspace.root / '.claude' / 'skills' / 'upload-skill'
        names = sorted(
            str(p.relative_to(skill_dir))
            for p in skill_dir.rglob('*')
            if p.is_file()
        )
        assert not any(
            part.startswith('._') or part == '.DS_Store' or part == '__MACOSX'
            for name in names
            for part in name.split('/')
        ), names
        assert 'assets/template.png' in names
        assert (skill_dir / 'assets' / 'model.bin').read_bytes() == BINARY


class TestPutPathFallback:
    async def test_put_path_is_origin_independent_and_works(
        self, upload_client, real_workspace
    ):
        """Behind a reverse proxy put_url's origin may be wrong; the
        contract is put_path — PUT to the client's own MCP origin +
        put_path must work identically."""
        minted = await _begin(upload_client)
        assert minted['put_path'].startswith('/api/skill-uploads/')
        assert 'sig=' in minted['put_path']
        assert minted['put_url'].endswith(minted['put_path'])

        put = await upload_client.put(
            minted['put_path'],
            content=_archive_bytes(_good_entries()),
            headers={'Content-Type': 'application/gzip'},
        )
        assert put.status_code == 200, put.text
        imported, envelope = await call_tool(
            upload_client,
            'manage_skill',
            {'action': 'import', 'upload_id': minted['upload_id']},
        )
        assert envelope['isError'] is False, imported
        skill_dir = real_workspace.root / '.claude' / 'skills' / 'upload-skill'
        assert (skill_dir / 'assets' / 'model.bin').read_bytes() == BINARY

    async def test_forwarded_headers_hint_the_origin(self, upload_client):
        r = await upload_client.post(
            MCP_PATH,
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {
                    'name': 'manage_skill',
                    'arguments': {'action': 'begin_upload'},
                },
            },
            headers={
                'X-Forwarded-Proto': 'https',
                'X-Forwarded-Host': 'seller.example.com',
            },
        )
        body = r.json()
        minted = json.loads(body['result']['content'][0]['text'])
        assert minted['put_url'].startswith(
            'https://seller.example.com/api/skill-uploads/'
        ), minted['put_url']


class TestMultipartBounded:
    async def _staging_clean(self, upload_staging, upload_id):
        archive = upload_staging / upload_id / 'archive.bin'
        assert not archive.exists(), 'oversize body left staged bytes'

    async def test_oversize_declared_multipart_413(
        self, upload_client, upload_staging, monkeypatch
    ):
        monkeypatch.setattr(skill_uploads, 'MAX_ARCHIVE_UPLOAD_BYTES', 64)
        monkeypatch.setattr('app.routers.skill_uploads.MULTIPART_OVERHEAD', 64)
        minted = await _begin(upload_client)
        parsed = urlparse(minted['put_url'])
        response = await upload_client.post(
            f'{parsed.path}?{parsed.query}',
            files={'file': ('a.tar.gz', b'z' * 4096, 'application/gzip')},
        )
        assert response.status_code == 413, response.text
        await self._staging_clean(upload_staging, minted['upload_id'])

    async def test_chunked_multipart_without_length_is_411(
        self, upload_client, upload_staging
    ):
        """Multipart without a declared length is refused outright:
        Starlette spools file parts UNBOUNDED, so a chunked body must
        never reach the parser."""
        minted = await _begin(upload_client)
        parsed = urlparse(minted['put_url'])
        head = (
            b'--X\r\nContent-Disposition: form-data; name="file"; '
            b'filename="a.tar.gz"\r\nContent-Type: application/gzip'
            b'\r\n\r\n'
        )
        body = head + b'z' * 4096 + b'\r\n--X--\r\n'

        async def gen():
            for i in range(0, len(body), 512):
                yield body[i : i + 512]

        response = await upload_client.post(
            f'{parsed.path}?{parsed.query}',
            content=gen(),
            headers={'Content-Type': 'multipart/form-data; boundary=X'},
        )
        assert response.status_code == 411, response.text
        await self._staging_clean(upload_staging, minted['upload_id'])
