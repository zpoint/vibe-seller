"""Run one git command in the workspace and drain its output."""

import asyncio
from pathlib import Path

from app.workspace.ignored_paths import git_env


async def run_git(
    root: Path, *args: str, check: bool = True
) -> asyncio.subprocess.Process:
    """Run ``git *args`` in *root*; raise on failure when *check*.

    The output is drained with ``communicate()``, never left in the
    pipes for a bare ``wait()``: git can write more than a pipe buffer
    holds (on Windows, ``add -A`` prints an LF->CRLF warning per file),
    and with nobody reading it blocks on the write forever. The caller
    holds the workspace's git lock across this, so one stuck git used
    to wedge every later auto-commit (store create, skill save) — see
    tests/unit/test_workspace_git_pipes.py.

    GIT_*_NAME/EMAIL come from ``git_env`` (setdefault; a real identity
    wins), which keeps the initial commit working on hosts with no
    global ``git config user.email/name`` (#181) without touching
    .git/config.
    """
    proc = await asyncio.create_subprocess_exec(
        'git',
        *args,
        cwd=str(root),
        env=git_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if check and proc.returncode != 0:
        raise RuntimeError(
            f'git {" ".join(args)} failed: {err.decode(errors="replace")}'
        )
    return proc
