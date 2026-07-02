"""Unit test for platform.reap_task_agents().

The cross-platform backstop that kills orphaned task-agent process trees
when the server is stopped without a graceful shutdown (notably Windows
TerminateProcess, which `vibe-seller stop` / the tray quit-restart use).
Pins: it matches only the vibe-seller headless-agent / browser-daemon
signatures (never an unrelated Claude session), kills each match plus its
child tree, and never kills its own pid.
"""

from unittest import mock

import pytest

from app import platform as plat

pytestmark = pytest.mark.unit


def _proc(pid, cmdline):
    p = mock.MagicMock()
    p.info = {'pid': pid, 'cmdline': cmdline}
    p.pid = pid
    p.children = mock.MagicMock(return_value=[])
    p.terminate = mock.MagicMock()
    p.kill = mock.MagicMock()
    return p


@pytest.mark.asyncio
async def test_reaps_agent_and_daemon_not_unrelated(monkeypatch):
    agent = _proc(
        101,
        [
            'claude',
            '-p',
            '--output-format',
            'stream-json',
            '--input-format',
            'stream-json',
        ],
    )
    child = _proc(102, ['browser-use', 'open', 'https://x'])
    agent.children = mock.MagicMock(return_value=[child])
    daemon = _proc(103, ['python', '-m', 'browser_use.skill_cli.daemon'])
    # Orphaned wrapper poll-loop (reparented after an ungraceful -9).
    wrapper = _proc(
        106,
        [
            'bash',
            '/home/u/.vibe-seller/bin/acme-store/browser-use',
            'eval',
            'var',
        ],
    )
    interactive = _proc(104, ['claude'])  # a plain Claude session
    editor = _proc(105, ['vim', 'notes.txt'])  # unrelated
    # An editor VIEWING the wrapper script — references the path but has
    # no browser-use subcommand, so it must NOT be reaped.
    editor_wrapper = _proc(
        107, ['vim', '/home/u/.vibe-seller/bin/acme-store/browser-use']
    )

    procs = [agent, child, daemon, wrapper, interactive, editor, editor_wrapper]
    monkeypatch.setattr(plat.psutil, 'process_iter', lambda attrs=None: procs)
    monkeypatch.setattr(
        plat.psutil, 'wait_procs', lambda ps, timeout=None: (ps, [])
    )
    monkeypatch.setattr(plat.os, 'getpid', lambda: 999)

    reaped = await plat.reap_task_agents()

    # Three roots matched (agent + daemon + orphaned wrapper loop); the
    # interactive claude, the editor, and an editor merely viewing the
    # wrapper script are left alone.
    assert reaped == 3
    agent.terminate.assert_called_once()
    child.terminate.assert_called_once()  # child tree of the agent
    daemon.terminate.assert_called_once()
    wrapper.terminate.assert_called_once()
    interactive.terminate.assert_not_called()
    editor.terminate.assert_not_called()
    editor_wrapper.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_pids_scoping_only_reaps_given_roots(monkeypatch):
    """With pids=, only the given roots (+ their trees) are reaped —
    process_iter is not even scanned. Pins the multi-server safety fix:
    `vibe-seller stop` reaps only the stopped server's own agents."""
    mine = _proc(201, ['claude', '-p', '--output-format', 'stream-json'])
    other = _proc(202, ['claude', '-p', '--output-format', 'stream-json'])

    def _proc_by_pid(pid):
        return {201: mine, 202: other}[pid]

    monkeypatch.setattr(plat.psutil, 'Process', _proc_by_pid)
    monkeypatch.setattr(
        plat.psutil, 'wait_procs', lambda ps, timeout=None: (ps, [])
    )
    monkeypatch.setattr(plat.os, 'getpid', lambda: 999)
    # process_iter must NOT be consulted when pids is given.
    monkeypatch.setattr(
        plat.psutil,
        'process_iter',
        lambda attrs=None: (_ for _ in ()).throw(AssertionError('scanned')),
    )

    reaped = await plat.reap_task_agents(pids={201})

    assert reaped == 1
    mine.terminate.assert_called_once()
    other.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_never_reaps_self(monkeypatch):
    me = _proc(
        999,
        [
            'claude',
            '-p',
            '--output-format',
            'stream-json',
            '--input-format',
            'stream-json',
        ],
    )
    monkeypatch.setattr(plat.psutil, 'process_iter', lambda attrs=None: [me])
    monkeypatch.setattr(
        plat.psutil, 'wait_procs', lambda ps, timeout=None: (ps, [])
    )
    monkeypatch.setattr(plat.os, 'getpid', lambda: 999)

    assert await plat.reap_task_agents() == 0
    me.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_force_kills_survivors(monkeypatch):
    agent = _proc(
        201,
        [
            'claude',
            '-p',
            '--output-format',
            'stream-json',
            '--input-format',
            'stream-json',
        ],
    )
    monkeypatch.setattr(plat.psutil, 'process_iter', lambda attrs=None: [agent])
    # Survives terminate → must be force-killed.
    monkeypatch.setattr(
        plat.psutil, 'wait_procs', lambda ps, timeout=None: ([], list(ps))
    )
    monkeypatch.setattr(plat.os, 'getpid', lambda: 999)

    await plat.reap_task_agents()
    agent.terminate.assert_called_once()
    agent.kill.assert_called_once()
