"""The skills tree has ONE mutation lock — writers and the copy both wait.

A task-workspace copy that catches a skill swap mid-flight gets a
partial ``.claude/skills`` dir, and the agent loads it that way. The
contract (``app.workspace.skills_tree``): every mutation of the tree
AND the copy into a task workspace run under the same lock. These
tests pin it — without the lock, the mutation/copy below completes
while the lock is "held" and the assertions fail.
"""

import asyncio

import pytest

from app.workspace import skills_tree
from app.workspace.manager import WorkspaceManager

pytestmark = pytest.mark.unit


def _make_skill(root, slug: str) -> None:
    skill_dir = root / '.claude' / 'skills' / slug
    skill_dir.mkdir(parents=True)
    (skill_dir / 'SKILL.md').write_text(f'# {slug}')


async def test_skill_delete_waits_for_the_tree_lock(tmp_path):
    manager = WorkspaceManager(root=tmp_path)
    _make_skill(tmp_path, 'demo')

    async with skills_tree.hold():
        task = asyncio.create_task(manager.delete_skill('demo'))
        await asyncio.sleep(0.05)
        assert not task.done(), (
            'delete_skill mutated the skills tree while the tree lock '
            'was held — a task-workspace copy could have caught it '
            'mid-swap'
        )
        assert (tmp_path / '.claude' / 'skills' / 'demo').exists()

    await task
    assert not (tmp_path / '.claude' / 'skills' / 'demo').exists()


async def test_task_workspace_copy_waits_for_the_tree_lock(
    tmp_path, monkeypatch
):
    manager = WorkspaceManager(root=tmp_path)
    _make_skill(tmp_path, 'demo')

    async def _skip_init(self):
        return None

    # The copy is the operation under test; the venv/git init around
    # it is not.
    monkeypatch.setattr(WorkspaceManager, 'ensure_init', _skip_init)

    async with skills_tree.hold():
        task = asyncio.create_task(manager.prepare_task_workspace('t' * 32))
        await asyncio.sleep(0.05)
        assert not task.done(), (
            'prepare_task_workspace copied the skills tree while the '
            'tree lock was held — it could have caught a writer '
            'mid-swap'
        )

    task_dir = await task
    assert (task_dir / '.claude' / 'skills' / 'demo' / 'SKILL.md').exists()
