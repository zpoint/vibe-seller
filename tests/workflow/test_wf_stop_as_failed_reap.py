"""The external-stop writer reaps: C2's stop_as_failed + C3's reaper.

C2 (#342) made ``stop_as_failed`` the ONE writer for every external
stop of a live task — the user's Stop AND a terminal browser-start
failure. The stop route used to own C3's reaping inline; the
single-writer refactor moved the write, and the reaping had to move
WITH it (resolved in the #342 merge): a task ended by a terminal
start failure must still have every ``VIBE_TASK_ID``-carrying process
it left behind killed, and another task's child must survive.

The negative control is the merge without the reap line: with it
removed from ``stop_as_failed``, the same test fails (the child
survives).
"""

import asyncio
import os
import subprocess

import pytest

from app.browser.inject.errors import ClientNotRunning
from app.browser.start_failure import fail_task_for_start_error
from app.task_reaper import reap_task_processes
from app.task_states import TaskStatus
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

_linux_only = pytest.mark.skipif(
    not os.path.isdir('/proc'), reason='procfs env scan (Linux/WSL)'
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f'/proc/{pid}/stat') as fh:
            return fh.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except (OSError, IndexError):
        return True


def _spawn_detached(env: dict) -> subprocess.Popen:
    """A child that escapes this process's group — the incident's
    ``nohup`` / ``ssh -f`` shape, carrying the task's VIBE_TASK_ID."""
    return subprocess.Popen(
        ['setsid', 'sh', '-c', 'exec sleep 120'],
        env={**os.environ, **env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@_linux_only
async def test_terminal_start_failure_reaps_the_tasks_processes(
    admin_client, install_fake_agent
):
    # a live task on a store (the shape fail_task_for_start_error
    # selects for: RUNNING, this store's own)
    store_id = (
        await admin_client.post('/api/stores', json={'name': 'Reap Store'})
    ).json()['id']
    # gate-held scenario: the agent stays live until released (the
    # proven pattern from test_wf_concurrency)
    gate = asyncio.Event()
    install_fake_agent.default_scenario = FakeAgentScenario(gate=gate)
    task_id = (
        await admin_client.post(
            '/api/tasks',
            json={'title': 'terminal start failure reap', 'store_id': store_id},
        )
    ).json()['id']
    await install_fake_agent.wait_started(task_id)
    task = (await admin_client.get(f'/api/tasks/{task_id}')).json()
    assert task['status'] in (
        str(TaskStatus.RUNNING),
        str(TaskStatus.DESIGNING),
    )

    # the incident's leftover: a detached child carrying the task id
    mine = _spawn_detached({'VIBE_TASK_ID': task_id})
    # and another task's child, which must NOT be touched
    other = _spawn_detached({'VIBE_TASK_ID': '00000000-0-0-0-000000'})

    try:
        # the terminal browser-start failure path (C2's contract)
        stopped = await fail_task_for_start_error(
            task_id, ClientNotRunning('client is not running'), store_id, 's'
        )
        assert stopped

        task = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert task['status'] == str(TaskStatus.FAILED)
        assert task['error_category'] == 'client_not_running'

        # the reaper ran on the external-stop path: MY detached child
        # is dead, the other task's is alive
        for _ in range(50):
            if not _alive(mine.pid):
                break
            await asyncio.sleep(0.1)
        assert not _alive(mine.pid), (
            'the terminal-start-failure stop left a detached '
            'VIBE_TASK_ID child alive'
        )
        assert _alive(other.pid)
    finally:
        gate.set()
        reap_task_processes('00000000-0-0-0-000000')
        for proc in (mine, other):
            try:
                proc.kill()
                proc.wait(timeout=1)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                pass
