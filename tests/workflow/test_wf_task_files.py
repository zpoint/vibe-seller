"""Workflow tests for task file listing and download.

Covers: recursive directory listing, subdirectory downloads,
symlink/dotfile exclusion, path traversal protection, zip download.
"""

from io import BytesIO
from unittest.mock import patch
import zipfile

import pytest

pytestmark = pytest.mark.workflow

FAKE_TASK_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


@pytest.fixture
def task_dir(tmp_path):
    """Create a fake task workspace with nested files."""
    td = tmp_path / 'tasks' / FAKE_TASK_ID
    td.mkdir(parents=True)

    # Top-level file
    (td / 'report.csv').write_text('col1,col2\na,b')

    # Subdirectory with files
    outputs = td / 'outputs'
    outputs.mkdir()
    (outputs / 'doc-001.pdf').write_bytes(b'fake-content-1')
    (outputs / 'doc-002.pdf').write_bytes(b'fake-content-2')

    # Nested subdirectory
    sub = outputs / 'region-a'
    sub.mkdir()
    (sub / 'doc-sub-001.pdf').write_bytes(b'fake-content-sub')

    # Dotfile and dotdir — should be excluded
    (td / '.mcp.json').write_text('{}')
    (td / '.claude').mkdir()
    (td / '.claude' / 'settings.json').write_text('{}')

    # Symlink — should be excluded. Skip on platforms without symlink perms.
    try:
        (td / 'knowledge').symlink_to('/tmp')
    except (OSError, NotImplementedError):
        pass

    # CLAUDE.md — should be excluded
    (td / 'CLAUDE.md').write_text('# skip')

    return td


@pytest.fixture
def _patch_tasks_dir(task_dir):
    """Patch _TASKS_DIR to point to our temp directory."""
    tasks_root = task_dir.parent
    with patch('app.routers.tasks_files._TASKS_DIR', tasks_root):
        yield


class TestListTaskFiles:
    async def test_lists_top_level_files(self, admin_client, _patch_tasks_dir):
        r = await admin_client.get(f'/api/tasks/{FAKE_TASK_ID}/files')
        assert r.status_code == 200
        names = [f['name'] for f in r.json()]
        assert 'report.csv' in names

    async def test_lists_subdirectory_files(
        self, admin_client, _patch_tasks_dir
    ):
        r = await admin_client.get(f'/api/tasks/{FAKE_TASK_ID}/files')
        names = [f['name'] for f in r.json()]
        assert 'outputs/doc-001.pdf' in names
        assert 'outputs/doc-002.pdf' in names

    async def test_lists_nested_subdirectory_files(
        self, admin_client, _patch_tasks_dir
    ):
        r = await admin_client.get(f'/api/tasks/{FAKE_TASK_ID}/files')
        names = [f['name'] for f in r.json()]
        assert 'outputs/region-a/doc-sub-001.pdf' in names

    async def test_excludes_dotfiles_and_infra(
        self, admin_client, _patch_tasks_dir
    ):
        r = await admin_client.get(f'/api/tasks/{FAKE_TASK_ID}/files')
        names = [f['name'] for f in r.json()]
        for n in names:
            assert not n.startswith('.')
            assert '.claude' not in n
            assert n != 'CLAUDE.md'
            assert n != '.mcp.json'

    async def test_excludes_symlinks(self, admin_client, _patch_tasks_dir):
        r = await admin_client.get(f'/api/tasks/{FAKE_TASK_ID}/files')
        names = [f['name'] for f in r.json()]
        assert not any('knowledge' in n for n in names)

    async def test_returns_correct_metadata(
        self, admin_client, _patch_tasks_dir
    ):
        r = await admin_client.get(f'/api/tasks/{FAKE_TASK_ID}/files')
        files = {f['name']: f for f in r.json()}
        csv = files['report.csv']
        assert csv['size'] == len('col1,col2\na,b')
        assert csv['type'] == 'text/csv'
        assert 'modified_at' in csv

        pdf = files['outputs/doc-001.pdf']
        assert pdf['type'] == 'application/pdf'

    async def test_empty_dir_returns_empty(
        self, admin_client, _patch_tasks_dir, task_dir
    ):
        # Remove all real files, keep only excluded ones
        for f in task_dir.rglob('*'):
            if f.is_file() and not f.name.startswith('.'):
                if f.name != 'CLAUDE.md':
                    f.unlink()
        r = await admin_client.get(f'/api/tasks/{FAKE_TASK_ID}/files')
        assert r.status_code == 200
        # Only excluded files remain, so list should be empty
        names = [f['name'] for f in r.json()]
        assert names == []


class TestDownloadTaskFile:
    async def test_download_top_level_file(
        self, admin_client, _patch_tasks_dir
    ):
        r = await admin_client.get(
            f'/api/tasks/{FAKE_TASK_ID}/files/report.csv'
        )
        assert r.status_code == 200
        assert b'col1,col2' in r.content

    async def test_download_subdirectory_file(
        self, admin_client, _patch_tasks_dir
    ):
        r = await admin_client.get(
            f'/api/tasks/{FAKE_TASK_ID}/files/outputs/doc-001.pdf'
        )
        assert r.status_code == 200
        assert r.content == b'fake-content-1'

    async def test_download_nested_subdirectory_file(
        self, admin_client, _patch_tasks_dir
    ):
        r = await admin_client.get(
            f'/api/tasks/{FAKE_TASK_ID}/files/outputs/region-a/doc-sub-001.pdf'
        )
        assert r.status_code == 200
        assert r.content == b'fake-content-sub'

    async def test_path_traversal_rejected(
        self, admin_client, _patch_tasks_dir
    ):
        r = await admin_client.get(
            f'/api/tasks/{FAKE_TASK_ID}/files/../../etc/passwd'
        )
        # Must not serve the file — 400 or 404 both acceptable
        assert r.status_code in (400, 404)

    async def test_nonexistent_file_404(self, admin_client, _patch_tasks_dir):
        r = await admin_client.get(
            f'/api/tasks/{FAKE_TASK_ID}/files/outputs/nope.pdf'
        )
        assert r.status_code == 404

    async def test_download_rejects_infra_files(
        self, admin_client, _patch_tasks_dir
    ):
        # Must not serve .mcp.json, CLAUDE.md, or .claude/*
        for path in ('.mcp.json', 'CLAUDE.md', '.claude/settings.json'):
            r = await admin_client.get(
                f'/api/tasks/{FAKE_TASK_ID}/files/{path}'
            )
            assert r.status_code == 404, (
                f'Expected 404 for {path}, got {r.status_code}'
            )


class TestDownloadTaskFilesZip:
    async def test_zip_contains_all_files(self, admin_client, _patch_tasks_dir):
        r = await admin_client.get(f'/api/tasks/{FAKE_TASK_ID}/files-zip')
        assert r.status_code == 200
        assert r.headers['content-type'] == 'application/zip'

        zf = zipfile.ZipFile(BytesIO(r.content))
        names = sorted(zf.namelist())
        assert 'report.csv' in names
        assert 'outputs/doc-001.pdf' in names
        assert 'outputs/doc-002.pdf' in names
        assert 'outputs/region-a/doc-sub-001.pdf' in names

    async def test_zip_excludes_dotfiles_and_infra(
        self, admin_client, _patch_tasks_dir
    ):
        r = await admin_client.get(f'/api/tasks/{FAKE_TASK_ID}/files-zip')
        zf = zipfile.ZipFile(BytesIO(r.content))
        for name in zf.namelist():
            assert not name.startswith('.')
            assert '.claude' not in name
            assert name != 'CLAUDE.md'

    async def test_zip_file_content_correct(
        self, admin_client, _patch_tasks_dir
    ):
        r = await admin_client.get(f'/api/tasks/{FAKE_TASK_ID}/files-zip')
        zf = zipfile.ZipFile(BytesIO(r.content))
        assert zf.read('report.csv') == b'col1,col2\na,b'
        assert zf.read('outputs/doc-001.pdf') == b'fake-content-1'
        assert (
            zf.read('outputs/region-a/doc-sub-001.pdf') == b'fake-content-sub'
        )

    async def test_zip_404_for_missing_task(
        self, admin_client, _patch_tasks_dir
    ):
        r = await admin_client.get('/api/tasks/nonexistent-task-id/files-zip')
        assert r.status_code in (400, 404)
