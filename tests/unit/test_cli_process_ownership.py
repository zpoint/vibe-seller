"""No CLI process the server starts outlives what started it.

The Google Workspace e2e saw a ``gws`` still running after its sign-in
was cancelled. Two ways the server could leave one behind are pinned
here: a status probe whose request went away (only its own timeout
killed it), and a sign-in step cancelled a second time while it was
still stopping its CLI (the kill came after an await that a second
cancel could interrupt).
"""

import asyncio
from pathlib import Path
import sys

import psutil
import pytest

from app.cli_integrations import login, manager
from app.cli_integrations.registry import INTEGRATIONS

pytestmark = pytest.mark.unit

SLEEPER = [sys.executable, '-c', 'import time; time.sleep(60)']


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


async def test_a_status_probe_dies_with_the_request_waiting_on_it(
    tmp_path, monkeypatch
):
    spawned: list[int] = []
    running = asyncio.Event()
    real_exec = asyncio.create_subprocess_exec

    async def exec_sleeper(*_argv, **kwargs):
        proc = await real_exec(*SLEEPER, **kwargs)
        spawned.append(proc.pid)
        running.set()
        return proc

    monkeypatch.setattr(
        manager.paths, 'binary_path', lambda *_a: Path(sys.executable)
    )
    monkeypatch.setattr(manager, 'run_env', lambda *_a: {})
    monkeypatch.setattr(manager.paths, 'state_dir', lambda *_a: tmp_path)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', exec_sleeper)

    run = await manager._runner(INTEGRATIONS['gws'], tmp_path)
    probe = asyncio.create_task(run(['auth', 'status']))
    await running.wait()
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe
    assert not _alive(spawned[0]), 'an abandoned status probe kept running'


async def test_a_second_cancel_during_cleanup_still_stops_the_cli():
    proc = await asyncio.create_subprocess_exec(*SLEEPER)
    started = asyncio.Event()

    async def step():
        try:
            started.set()
            await asyncio.sleep(60)
        finally:
            await login._terminate_fully(proc)

    task = asyncio.create_task(step())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)  # the step is now inside its cleanup
    task.cancel()  # e.g. the request awaiting the cancel went away too
    with pytest.raises(asyncio.CancelledError):
        await task
    # Give a shielded kill its moment to land.
    for _ in range(50):
        if not _alive(proc.pid):
            break
        await asyncio.sleep(0.1)
    assert not _alive(proc.pid), 'the sign-in CLI outlived its cancel'
