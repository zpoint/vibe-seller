"""``WorkspaceManager._run_git`` drains git's output.

It used to ``wait()`` on a git process whose stdout/stderr were pipes
nobody read. Once git wrote more than a pipe buffer holds it blocked on
the write, ``wait()`` never returned and ``_git_lock`` stayed held, so
every later auto-commit hung behind it. Seen on the Windows installer
e2e: ``git add -A`` over a freshly installed skill tree printed an
LF→CRLF warning per file, and the next ``POST /api/stores`` (which
auto-commits) never answered.

Reproduced on any OS here with output larger than any pipe buffer (64
KiB on Linux/macOS, less on Windows): ``cat-file`` of a 512 KiB blob.
"""

import asyncio
import subprocess

import pytest

from app.workspace.manager import WorkspaceManager

pytestmark = pytest.mark.unit

BIG = 512 * 1024


def _repo_with_big_blob(root):
    root.mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    (root / 'big.txt').write_text('x' * BIG)
    return subprocess.run(
        ['git', 'hash-object', '-w', 'big.txt'],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


async def test_git_output_beyond_a_pipe_buffer_does_not_wedge(tmp_path):
    root = tmp_path / 'ws'
    blob = _repo_with_big_blob(root)
    mgr = WorkspaceManager(root=root)

    proc = await asyncio.wait_for(mgr._run_git('cat-file', '-p', blob), 20)
    assert proc.returncode == 0
    # The lock is free again: the next git call goes straight through.
    proc = await asyncio.wait_for(mgr._run_git('status', '--short'), 20)
    assert proc.returncode == 0


async def test_a_failing_git_still_raises_with_its_stderr(tmp_path):
    root = tmp_path / 'ws'
    _repo_with_big_blob(root)
    mgr = WorkspaceManager(root=root)
    with pytest.raises(RuntimeError, match='not-a-real-object'):
        await asyncio.wait_for(
            mgr._run_git('cat-file', '-p', 'not-a-real-object'), 20
        )
