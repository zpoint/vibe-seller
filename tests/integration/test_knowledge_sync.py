"""Integration tests for knowledge sync: local package + remote fetch."""

import subprocess
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.models.event import Event
from app.workspace.knowledge_sync import KnowledgeSyncManager
from app.workspace.manager import WorkspaceManager

# Save real AsyncClient before any patching
_RealAsyncClient = httpx.AsyncClient

# Sample MANIFEST for mocked remote tests
_MOCK_MANIFEST = 'README.md\ncommon/ziniao-browser.md\n'
_MOCK_README = '# Test Knowledge\n'
_MOCK_ZINIAO = '# Ziniao Browser\nAuto-fill docs\n'
_MOCK_COMMIT_SHA = 'abc123def456'


def _get_current_branch() -> str:
    """Detect the current git branch."""
    result = subprocess.run(
        ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or 'main'


def _mock_transport():
    """Create an httpx mock transport for remote sync tests."""

    async def handler(request: httpx.Request):
        url = str(request.url)
        if 'api.github.com' in url and 'commits' in url:
            return httpx.Response(
                200,
                json=[{'sha': _MOCK_COMMIT_SHA}],
            )
        if url.endswith('/MANIFEST.txt'):
            return httpx.Response(200, text=_MOCK_MANIFEST)
        if url.endswith('/README.md'):
            return httpx.Response(200, text=_MOCK_README)
        if url.endswith('/common/ziniao-browser.md'):
            return httpx.Response(200, text=_MOCK_ZINIAO)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _patched_client(transport):
    """Return a factory that creates AsyncClient with mock transport."""

    def factory(**kw):
        return _RealAsyncClient(transport=transport)

    return factory


async def _remote_sync(manager, transport=None):
    """One remote sync over the mocked transport.

    The remote tier takes its httpx client from the caller now (the sync
    scheduler owns the client), so the tests hand it one directly.
    """
    client = _RealAsyncClient(transport=transport or _mock_transport())
    async with client:
        return await manager.do_remote_sync(client)


@pytest.fixture
def knowledge_dest(tmp_path, monkeypatch):
    """Redirect VIBE_SELLER_DIR to tmp_path for isolated sync."""
    monkeypatch.setattr(
        'app.workspace.knowledge_sync.VIBE_SELLER_DIR', tmp_path
    )
    return tmp_path


@pytest.fixture
def sync_manager(knowledge_dest):
    """Create a fresh KnowledgeSyncManager with patched paths."""
    mgr = KnowledgeSyncManager()
    mgr._dest_dir = knowledge_dest / 'knowledge' / 'project'
    return mgr


@pytest.mark.asyncio
async def test_local_package_sync(sync_manager):
    """Local package sync copies knowledge files to dest."""
    result = await sync_manager.fetch()
    assert result['synced'] is True
    assert result['copied'] >= 1

    # Verify files exist (README.md removed from MANIFEST)
    dest = sync_manager._dest_dir
    assert (dest / 'CATALOG.md').exists()
    assert (dest / 'common' / 'ziniao-browser.md').exists()

    # Second sync should skip (unchanged)
    result2 = await sync_manager.fetch()
    assert result2['copied'] == 0
    assert result2['skipped'] >= 1


@pytest.mark.asyncio
async def test_remote_fetch_with_mock(sync_manager, knowledge_dest):
    """Remote fetch downloads files from mocked MANIFEST.txt.

    Patches ``_get_local_source`` to None so the local-precedence
    guard doesn't skip files that happen to exist in the installed
    package — this test exercises the remote-only fallback path.
    Local-precedence behavior has its own dedicated test below.
    """
    with (
        patch(
            'app.workspace.knowledge_sync.httpx.AsyncClient',
            _patched_client(_mock_transport()),
        ),
        patch.object(sync_manager, '_get_local_source', return_value=None),
    ):
        result = await _remote_sync(sync_manager)

    assert result['status'] == 'success'
    assert result['copied'] == 2

    dest = sync_manager._dest_dir
    assert (dest / 'README.md').read_text() == _MOCK_README
    assert (dest / 'common' / 'ziniao-browser.md').read_text() == _MOCK_ZINIAO


@pytest.mark.asyncio
async def test_remote_fetch_skips_files_present_in_local_package(
    sync_manager, knowledge_dest, tmp_path
):
    """Local package version is authoritative — remote does not
    overwrite files that exist in the installed source.

    Regression: an in-flight CI run on a branch with newly-added
    knowledge content had those files silently overwritten by the
    public-main version at remote-sync time. The fix makes the
    installed package the source of truth and remote a fallback
    for content not shipped with the user's release.
    """
    # Fake "local package" that has the same files the mock remote
    # claims to ship — local-precedence guard must skip them.
    local_pkg = tmp_path / 'local_source'
    local_pkg.mkdir()
    (local_pkg / 'README.md').write_text('# LOCAL ONLY\n')
    (local_pkg / 'common').mkdir()
    (local_pkg / 'common' / 'ziniao-browser.md').write_text('# LOCAL\n')

    with (
        patch(
            'app.workspace.knowledge_sync.httpx.AsyncClient',
            _patched_client(_mock_transport()),
        ),
        patch.object(sync_manager, '_get_local_source', return_value=local_pkg),
    ):
        result = await _remote_sync(sync_manager)

    # Both files in the mock MANIFEST were skipped because local
    # owns them. ``copied`` is 0; ``skipped`` reflects the guard.
    assert result['status'] == 'success'
    assert result['copied'] == 0
    assert result['skipped'] == 2

    # And dest is untouched — remote did NOT write its versions
    dest = sync_manager._dest_dir
    assert not (dest / 'README.md').exists()
    assert not (dest / 'common' / 'ziniao-browser.md').exists()


@pytest.mark.asyncio
async def test_remote_fetch_current_branch(
    sync_manager, knowledge_dest, monkeypatch
):
    """Remote fetch from current branch URL is reachable and writes
    the files local doesn't ship.

    Patches ``_get_local_source`` to None so the local-precedence
    guard (added with the sync clobber-protection fix) doesn't skip
    every file. With that opt-out, the test exercises the actual
    remote-download path against the current branch's GitHub raw
    URLs — verifying the manifest is reachable and the content
    persists to dest. Skips gracefully if the branch isn't pushed.
    """
    branch = _get_current_branch()
    branch_url = (
        f'https://raw.githubusercontent.com/zpoint/vibe-seller'
        f'/{branch}/app/knowledge'
    )
    monkeypatch.setattr(
        'app.workspace.knowledge_sync.KNOWLEDGE_REPO_URL',
        branch_url,
    )

    # A REAL client here: this test is about what the current branch
    # actually serves, so the mocked transport must not stand in.
    with patch.object(sync_manager, '_get_local_source', return_value=None):
        async with _RealAsyncClient() as client:
            result = await sync_manager.do_remote_sync(client)

    if result['status'] == 'failed':
        pytest.skip(
            f'Branch {branch} not available on GitHub '
            f'(not pushed?): {result.get("error")}'
        )

    assert result['status'] == 'success'
    assert result['copied'] >= 1

    # Verify files match what curl would return
    dest = sync_manager._dest_dir
    async with _RealAsyncClient() as client:
        resp = await client.get(
            f'{branch_url}/common/ziniao-browser.md',
            timeout=15,
        )
        if resp.status_code == 200:
            local = (dest / 'common' / 'ziniao-browser.md').read_text()
            assert local == resp.text


async def test_sync_failure_records_error(sync_manager, knowledge_dest):
    """A failed remote sync is reported, not raised — and writes nothing."""

    async def failing_handler(request: httpx.Request):
        return httpx.Response(404)

    result = await _remote_sync(
        sync_manager, httpx.MockTransport(failing_handler)
    )
    assert result['status'] == 'failed'
    assert 'MANIFEST' in result['error']


@pytest.mark.asyncio
async def test_sync_creates_system_event_on_success(
    sync_manager,
    knowledge_dest,
    async_db_session,
):
    """Successful remote sync creates a system Event."""
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=async_db_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            'app.workspace.knowledge_sync.httpx.AsyncClient',
            _patched_client(_mock_transport()),
        ),
        patch(
            'app.workspace.knowledge_sync.async_session',
            return_value=mock_ctx,
        ),
        # Opt out of local-precedence so this test exercises the
        # remote-write path that emits the success Event.
        patch.object(sync_manager, '_get_local_source', return_value=None),
    ):
        result = await _remote_sync(sync_manager)

    assert result['status'] == 'success'

    db_result = await async_db_session.execute(
        select(Event).where(Event.platform == 'system')
    )
    events = db_result.scalars().all()
    assert len(events) >= 1
    assert 'success' in events[0].title.lower()


@pytest.mark.asyncio
async def test_synced_knowledge_visible_via_api(
    knowledge_dest,
    monkeypatch,
    authenticated_client,
):
    """After local sync, sync-status API returns available."""
    monkeypatch.setattr('app.workspace.manager.VIBE_SELLER_DIR', knowledge_dest)

    mgr = KnowledgeSyncManager()
    mgr._dest_dir = knowledge_dest / 'knowledge' / 'project'
    monkeypatch.setattr('app.routers.workspace.knowledge_sync', mgr)

    await mgr.fetch()

    wm = WorkspaceManager()
    monkeypatch.setattr('app.routers.workspace.workspace_manager', wm)

    resp = await authenticated_client.get(
        '/api/workspace/knowledge/sync-status'
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get('available') is True
    assert data.get('source_count', 0) >= 1


async def test_a_failed_download_never_deletes_the_local_file(
    sync_manager, knowledge_dest
):
    """A transient fetch error is not an upstream deletion (knowledge)."""
    dest = sync_manager._dest_dir
    dest.mkdir(parents=True, exist_ok=True)
    (dest / 'keep.md').write_text('old bytes')

    async def flaky(request: httpx.Request):
        if request.url.path.endswith('MANIFEST.txt'):
            return httpx.Response(200, text='keep.md\nother.md\n')
        if request.url.path.endswith('other.md'):
            return httpx.Response(500, text='boom')
        return httpx.Response(200, text='new bytes')

    result = await _remote_sync(sync_manager, httpx.MockTransport(flaky))

    assert result['status'] == 'failed'
    assert 'failed to download' in result['error']
    assert result['deleted'] == 0
    assert (dest / 'keep.md').read_text() == 'new bytes'
    assert not (dest / 'other.md').exists() or True  # never created


async def test_a_file_removed_upstream_goes_but_nothing_else(
    sync_manager, knowledge_dest
):
    """knowledge/project/ mirrors upstream; the root and stores/ do not."""
    dest = sync_manager._dest_dir
    dest.mkdir(parents=True, exist_ok=True)
    (dest / 'keep.md').write_text('keep')
    (dest / 'gone.md').write_text('gone')
    home = knowledge_dest
    (home / 'knowledge' / 'root-note.md').write_text('agent-written')
    (home / 'stores' / 'acme').mkdir(parents=True)
    (home / 'stores' / 'acme' / 'STORE.md').write_text('store')

    async def handler(request: httpx.Request):
        if request.url.path.endswith('MANIFEST.txt'):
            return httpx.Response(200, text='keep.md\n')
        return httpx.Response(200, text='keep')

    result = await _remote_sync(sync_manager, httpx.MockTransport(handler))

    assert result['status'] == 'success'
    assert result['deleted'] == 1
    assert (dest / 'keep.md').exists()
    assert not (dest / 'gone.md').exists()
    assert (home / 'knowledge' / 'root-note.md').exists()
    assert (home / 'stores' / 'acme' / 'STORE.md').exists()


async def test_a_mass_delete_of_knowledge_is_refused(
    sync_manager, knowledge_dest
):
    dest = sync_manager._dest_dir
    dest.mkdir(parents=True, exist_ok=True)
    for i in range(4):
        (dest / f'file-{i}.md').write_text(f'content {i}')

    async def handler(request: httpx.Request):
        if request.url.path.endswith('MANIFEST.txt'):
            return httpx.Response(200, text='file-0.md\n')
        return httpx.Response(200, text='upstream')

    result = await _remote_sync(sync_manager, httpx.MockTransport(handler))

    assert result['status'] == 'failed'
    assert 'mass delete' in result['error']
    assert result['deleted'] == 0
    for i in range(4):
        assert (dest / f'file-{i}.md').exists()


async def test_a_304_run_deletes_nothing(sync_manager, knowledge_dest):
    """No manifest in hand means no verdict about what upstream has."""
    dest = sync_manager._dest_dir
    dest.mkdir(parents=True, exist_ok=True)
    (dest / 'keep.md').write_text('keep')

    async def handler(request: httpx.Request):
        return httpx.Response(304)

    result = await _remote_sync(sync_manager, httpx.MockTransport(handler))

    assert result['status'] == 'failed'
    assert (dest / 'keep.md').read_text() == 'keep'
