"""Tests for WorkspaceManager git operations: history, version, reset."""

from pathlib import Path

import git as gitlib
import pytest

from app.workspace.manager import WorkspaceManager


@pytest.fixture
def ws(tmp_path: Path) -> WorkspaceManager:
    """Create a WorkspaceManager with a temp git repo."""
    mgr = WorkspaceManager(root=tmp_path)
    # Init git repo
    repo = gitlib.Repo.init(str(tmp_path))
    # Configure git user for CI environments where it's not set
    repo.config_writer().set_value('user', 'name', 'Test').release()
    repo.config_writer().set_value('user', 'email', 'test@test').release()
    # Create .gitignore so there's an initial commit
    (tmp_path / '.gitignore').write_text('*.pyc\n')
    repo.index.add(['.gitignore'])
    repo.index.commit('Initial commit')
    # Create knowledge dir
    (tmp_path / 'knowledge' / 'project').mkdir(parents=True)
    return mgr


@pytest.fixture
def repo(ws: WorkspaceManager) -> gitlib.Repo:
    """Get the underlying git repo."""
    return gitlib.Repo(str(ws.root))


@pytest.mark.unit
class TestFileHistory:
    async def test_empty_history_for_new_file(self, ws: WorkspaceManager):
        """A file with no commits returns empty history."""
        f = ws.root / 'knowledge' / 'test.md'
        f.write_text('hello')
        history = await ws.file_history('knowledge/test.md')
        assert history == []

    async def test_single_commit(self, ws: WorkspaceManager, repo: gitlib.Repo):
        """File with one commit shows that commit."""
        f = ws.root / 'knowledge' / 'test.md'
        f.write_text('v1')
        repo.index.add(['knowledge/test.md'])
        repo.index.commit('Add test file')

        history = await ws.file_history('knowledge/test.md')
        assert len(history) == 1
        assert history[0]['message'] == 'Add test file'
        assert len(history[0]['sha']) == 12

    async def test_multiple_commits(
        self, ws: WorkspaceManager, repo: gitlib.Repo
    ):
        """File with multiple commits returns them newest-first."""
        f = ws.root / 'knowledge' / 'test.md'
        f.write_text('v1')
        repo.index.add(['knowledge/test.md'])
        repo.index.commit('First version')

        f.write_text('v2')
        repo.index.add(['knowledge/test.md'])
        repo.index.commit('Second version')

        f.write_text('v3')
        repo.index.add(['knowledge/test.md'])
        repo.index.commit('Third version')

        history = await ws.file_history('knowledge/test.md')
        assert len(history) == 3
        assert history[0]['message'] == 'Third version'
        assert history[2]['message'] == 'First version'

    async def test_max_count(self, ws: WorkspaceManager, repo: gitlib.Repo):
        """max_count limits returned commits."""
        f = ws.root / 'knowledge' / 'test.md'
        for i in range(5):
            f.write_text(f'v{i}')
            repo.index.add(['knowledge/test.md'])
            repo.index.commit(f'Version {i}')

        history = await ws.file_history('knowledge/test.md', max_count=2)
        assert len(history) == 2

    async def test_history_fields(
        self, ws: WorkspaceManager, repo: gitlib.Repo
    ):
        """Each commit entry has sha, message, date, author."""
        f = ws.root / 'knowledge' / 'test.md'
        f.write_text('content')
        repo.index.add(['knowledge/test.md'])
        repo.index.commit('Test commit')

        history = await ws.file_history('knowledge/test.md')
        entry = history[0]
        assert 'sha' in entry
        assert 'message' in entry
        assert 'date' in entry
        assert 'author' in entry


@pytest.mark.unit
class TestFileAtCommit:
    async def test_read_old_version(
        self, ws: WorkspaceManager, repo: gitlib.Repo
    ):
        """Can read file content at an older commit."""
        f = ws.root / 'knowledge' / 'test.md'
        f.write_text('original content')
        repo.index.add(['knowledge/test.md'])
        c1 = repo.index.commit('v1')

        f.write_text('updated content')
        repo.index.add(['knowledge/test.md'])
        repo.index.commit('v2')

        content = await ws.file_at_commit('knowledge/test.md', c1.hexsha)
        assert content == 'original content'

    async def test_read_current_version(
        self, ws: WorkspaceManager, repo: gitlib.Repo
    ):
        """Can read file content at the latest commit."""
        f = ws.root / 'knowledge' / 'test.md'
        f.write_text('latest')
        repo.index.add(['knowledge/test.md'])
        c = repo.index.commit('latest')

        content = await ws.file_at_commit('knowledge/test.md', c.hexsha)
        assert content == 'latest'

    async def test_short_sha(self, ws: WorkspaceManager, repo: gitlib.Repo):
        """Short SHA (prefix) works for file_at_commit."""
        f = ws.root / 'knowledge' / 'test.md'
        f.write_text('hello')
        repo.index.add(['knowledge/test.md'])
        c = repo.index.commit('short sha test')

        content = await ws.file_at_commit('knowledge/test.md', c.hexsha[:8])
        assert content == 'hello'

    async def test_invalid_commit_raises(self, ws: WorkspaceManager):
        """Invalid commit SHA raises an error."""
        f = ws.root / 'knowledge' / 'test.md'
        f.write_text('hello')
        with pytest.raises(Exception):
            await ws.file_at_commit('knowledge/test.md', 'deadbeef1234')


@pytest.mark.unit
class TestResetFileToCommit:
    async def test_reset_restores_content(
        self, ws: WorkspaceManager, repo: gitlib.Repo
    ):
        """Reset restores file to the content of a previous commit."""
        f = ws.root / 'knowledge' / 'test.md'
        f.write_text('v1')
        repo.index.add(['knowledge/test.md'])
        c1 = repo.index.commit('First')

        f.write_text('v2')
        repo.index.add(['knowledge/test.md'])
        repo.index.commit('Second')

        await ws.reset_file_to_commit('knowledge/test.md', c1.hexsha)
        assert f.read_text() == 'v1'

    async def test_reset_creates_commit(
        self, ws: WorkspaceManager, repo: gitlib.Repo
    ):
        """Reset creates a new revert commit."""
        f = ws.root / 'knowledge' / 'test.md'
        f.write_text('v1')
        repo.index.add(['knowledge/test.md'])
        c1 = repo.index.commit('First')

        f.write_text('v2')
        repo.index.add(['knowledge/test.md'])
        repo.index.commit('Second')

        commits_before = list(repo.iter_commits())

        await ws.reset_file_to_commit('knowledge/test.md', c1.hexsha)

        commits_after = list(repo.iter_commits())
        assert len(commits_after) == len(commits_before) + 1
        assert 'Revert' in commits_after[0].message

    async def test_reset_shows_in_history(
        self, ws: WorkspaceManager, repo: gitlib.Repo
    ):
        """After reset, file history includes the revert commit."""
        f = ws.root / 'knowledge' / 'test.md'
        f.write_text('v1')
        repo.index.add(['knowledge/test.md'])
        c1 = repo.index.commit('First')

        f.write_text('v2')
        repo.index.add(['knowledge/test.md'])
        repo.index.commit('Second')

        await ws.reset_file_to_commit('knowledge/test.md', c1.hexsha)

        history = await ws.file_history('knowledge/test.md')
        assert len(history) == 3
        assert 'Revert' in history[0]['message']


@pytest.mark.unit
class TestPathValidation:
    async def test_path_traversal_rejected(self, ws: WorkspaceManager):
        """Path traversal attempts are rejected."""
        with pytest.raises(ValueError, match='traversal'):
            await ws.file_history('../../../etc/passwd')

    async def test_path_traversal_in_reset(self, ws: WorkspaceManager):
        """Path traversal in reset is rejected."""
        with pytest.raises(ValueError, match='traversal'):
            await ws.reset_file_to_commit('../../../etc/passwd', 'abc123')


@pytest.mark.unit
class TestAutoCommitOnSave:
    async def test_write_file_commits(
        self, ws: WorkspaceManager, repo: gitlib.Repo
    ):
        """write_file auto-commits the change."""
        await ws.ensure_init()
        await ws.write_file('knowledge/test.md', 'saved content')

        # Check file on disk
        f = ws.root / 'knowledge' / 'test.md'
        assert f.read_text() == 'saved content'

        # Check git log
        history = await ws.file_history('knowledge/test.md')
        assert len(history) >= 1
        assert 'Update' in history[0]['message']

    async def test_write_file_rejects_l1(
        self, ws: WorkspaceManager, repo: gitlib.Repo
    ):
        """write_file rejects writes to L1 (knowledge/project/)."""
        await ws.ensure_init()
        with pytest.raises(ValueError, match='read-only L1 path'):
            await ws.write_file('knowledge/project/common/test.md', 'x')

    async def test_delete_file_rejects_l1(
        self, ws: WorkspaceManager, repo: gitlib.Repo
    ):
        """delete_file rejects deletion of L1 files."""
        await ws.ensure_init()
        p = ws.root / 'knowledge' / 'project' / 'test.md'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('test')
        with pytest.raises(ValueError, match='read-only L1 path'):
            await ws.delete_file('knowledge/project/test.md')

    async def test_write_file_rejects_l1_dot_dot(
        self, ws: WorkspaceManager, repo: gitlib.Repo
    ):
        """L1 guard catches paths with .. segments."""
        await ws.ensure_init()
        with pytest.raises(ValueError, match='read-only L1 path'):
            await ws.write_file('knowledge/../knowledge/project/test.md', 'x')

    async def test_write_file_allows_l2(
        self, ws: WorkspaceManager, repo: gitlib.Repo
    ):
        """write_file allows writes to L2 (knowledge/ root)."""
        await ws.ensure_init()
        await ws.write_file('knowledge/notes.md', 'L2 content')
        f = ws.root / 'knowledge' / 'notes.md'
        assert f.read_text() == 'L2 content'
