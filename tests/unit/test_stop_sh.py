"""``stop.sh --scoped`` stops only its own server and that server's kids.

The invariant --scoped pins (stop.sh <port> --scoped, for test/demo
servers on shared machines):

* S1 — selection is LISTEN-state + command-line based: only this
  project's ``uvicorn app.main:app`` listening on the port is a stop
  target. A process with merely a *client* connection to the port (a
  local proxy relaying to it is the real-world case) and any foreign
  listener are out of bounds;
* S2 — the selected server's own descendants are reaped from a
  pre-stop snapshot, and an unrelated process (even one that looks
  like a vibe-seller task agent) is never signalled;
* D1 — the default (non-scoped) path is unchanged: it still stops
  everyone attached to the port and still runs the machine-wide agent
  reap.

D1 runs with ``pgrep``/``pkill`` stubbed to *recording no-ops* so the
machine-wide reap can never actually signal anything while tests run
(that reap is exactly the blast radius the flag exists to avoid — the
suite must not reproduce it). The recorder proves the reap still runs.
"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import pytest

REPO = Path(__file__).resolve().parents[2]
STOP_SH = REPO / 'stop.sh'
STOP_LIB = REPO / 'scripts' / 'stop_lib.sh'

pytestmark = pytest.mark.unit

# The agent pattern reap_agents scans for machine-wide (kept in sync
# with stop.sh; the D1 stub assertions key off it).
AGENT_PATTERN = 'output-format stream-json --input-format stream-json'


def _free_port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _child_env() -> dict:
    """No proxy for loopback traffic: a local proxy (ClashX) relaying
    to our port would hold a socket on it — the very thing S1 exists
    to protect."""
    env = dict(os.environ)
    for key in ('http_proxy', 'HTTP_PROXY', 'https_proxy', 'HTTPS_PROXY'):
        env.pop(key, None)
    env['no_proxy'] = '127.0.0.1,localhost'
    env['NO_PROXY'] = '127.0.0.1,localhost'
    return env


def _faked_argv0_env() -> dict:
    """``_child_env`` plus where the interpreter's stdlib lives.

    The children below run with argv0 replaced by a server-shaped
    command line, which is what ``stop.sh`` must recognise. A
    relocatable CPython (uv's, on Linux) finds its own stdlib from
    argv0, so with argv0 faked it cannot start at all — measured: exit 1
    with the inherited env, exit 0 with ``PYTHONHOME`` set to
    ``sys.base_prefix``. The children import only the stdlib, so naming
    the base installation is all they need, on any interpreter.
    """
    return {**_child_env(), 'PYTHONHOME': sys.base_prefix}


def _fake_server_script(dir_: Path) -> Path:
    script = dir_ / 'fake_server.py'
    script.write_text(
        'import socket, subprocess, sys, time\n'
        'port = int(sys.argv[1])\n'
        'child_file = sys.argv[2]\n'
        # argv0 is faked, so sys.executable is unreliable in here (empty
        # on Linux) — the parent hands over the real interpreter.
        'python = sys.argv[3]\n'
        'srv = socket.socket()\n'
        'srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n'
        "srv.bind(('127.0.0.1', port))\n"
        'srv.listen(8)\n'
        "child = subprocess.Popen([python, '-c',\n"
        "                          'import time; time.sleep(300)'])\n"
        "open(child_file, 'w').write(str(child.pid))\n"
        'time.sleep(300)\n'
    )
    return script


def _spawn_uvicorn(script: Path, port: int, child_file: Path):
    """The project's server command line (argv0 faked) — what
    ``is_project_uvicorn`` matches on."""
    return subprocess.Popen(
        [
            'uvicorn app.main:app',
            str(script),
            str(port),
            str(child_file),
            sys.executable,
        ],
        executable=sys.executable,
        env=_faked_argv0_env(),
    )


def _spawn_sleeping_uvicorn():
    """Looks like the server but listens on nothing (PID-file path)."""
    return subprocess.Popen(
        ['uvicorn app.main:app', '-c', 'import time; time.sleep(300)'],
        executable=sys.executable,
        env=_faked_argv0_env(),
    )


def _spawn_client(port: int):
    # Keep the socket referenced — an unreferenced one is closed by
    # CPython the moment create_connection returns, and there would be
    # no client connection left to protect.
    return subprocess.Popen(
        [
            sys.executable,
            '-c',
            'import socket, time\n'
            f"sock = socket.create_connection(('127.0.0.1', {port}))\n"
            'time.sleep(300)\n',
        ],
        env=_child_env(),
    )


def _spawn_foreign_listener(port: int):
    return subprocess.Popen(
        [
            sys.executable,
            '-m',
            'http.server',
            str(port),
            '--bind',
            '127.0.0.1',
        ],
        env=_child_env(),
    )


def _spawn_agent_decoy():
    """Command line identical to a vibe-seller task agent — the thing
    reap_agents machine-reaps. Must survive every scoped stop."""
    return subprocess.Popen(
        ['claude -p ' + AGENT_PATTERN, '-c', 'import time; time.sleep(300)'],
        executable=sys.executable,
        env=_faked_argv0_env(),
    )


def _wait_port_listening(port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(('127.0.0.1', port), 0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f'nothing listening on {port}')


def _wait_dead(procs, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    for proc in procs:
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        assert proc.poll() is not None, f'{proc.args} survived'


def _kill(*procs) -> None:
    for proc in procs:
        if proc is None or proc.poll() is not None:
            continue
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


class _Scene:
    """Throwaway processes for one test, all killed on exit."""

    def __init__(self):
        self.procs = []

    def add(self, proc):
        # Reap promptly in the background: a dead fake that stays a
        # zombie of the test process still answers `kill -0`, which
        # would make stop_pid_graceful burn its full SIGTERM wait.
        # Real servers are orphaned and reaped by launchd/init, so
        # this models reality rather than papering over a bug.
        threading.Thread(target=proc.wait, daemon=True).start()
        self.procs.append(proc)
        return proc

    def close(self):
        _kill(*self.procs)


def _run_stop(*args, env=None, timeout=90):
    return subprocess.run(
        ['bash', str(STOP_SH), *[str(a) for a in args]],
        cwd=str(REPO),
        env=env or _child_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_scoped_stops_server_and_its_children_only(tmp_path):
    scene = _Scene()
    try:
        port = _free_port()
        child_file = tmp_path / 'child.pid'
        server = scene.add(
            _spawn_uvicorn(_fake_server_script(tmp_path), port, child_file)
        )
        _wait_port_listening(port)
        client = scene.add(_spawn_client(port))
        decoy = scene.add(_spawn_agent_decoy())
        time.sleep(0.2)
        assert client.poll() is None and decoy.poll() is None

        result = _run_stop(port, '--scoped')
        assert result.returncode == 0, result.stderr

        child_pid = int(child_file.read_text())
        _wait_dead([server])
        deadline = time.time() + 8
        while _pid_alive(child_pid) and time.time() < deadline:
            time.sleep(0.05)
        assert not _pid_alive(child_pid), 'server child was not reaped'
        # Nothing outside the server tree may be touched.
        assert client.poll() is None, 'client of the port was killed'
        assert decoy.poll() is None, 'unrelated agent-like proc killed'
    finally:
        scene.close()


def test_scoped_leaves_foreign_listener_and_client_alone():
    scene = _Scene()
    try:
        port = _free_port()
        listener = scene.add(_spawn_foreign_listener(port))
        _wait_port_listening(port)
        client = scene.add(_spawn_client(port))
        time.sleep(0.2)

        result = _run_stop(port, '--scoped')
        assert result.returncode == 0, result.stderr

        assert listener.poll() is None, 'foreign listener was killed'
        assert client.poll() is None, 'client of the port was killed'
    finally:
        scene.close()


def test_scoped_selection_picks_only_project_uvicorn(tmp_path):
    scene = _Scene()
    port_file = _free_port()
    pid_file = REPO / f'.pids_{port_file}'
    try:
        # A listener that is not this project's uvicorn is not selected.
        port_foreign = _free_port()
        scene.add(_spawn_foreign_listener(port_foreign))
        _wait_port_listening(port_foreign)
        scene.add(_spawn_client(port_foreign))
        assert _select(port_foreign) == ''

        # Our server listening on a port IS selected.
        port = _free_port()
        child_file = tmp_path / 'child.pid'
        server = scene.add(
            _spawn_uvicorn(_fake_server_script(tmp_path), port, child_file)
        )
        _wait_port_listening(port)
        assert _select(port) == str(server.pid)

        # A .pids file entry counts only when the command line still
        # matches (a reused PID must never be signalled)...
        plain = scene.add(_spawn_agent_decoy())
        pid_file.write_text(f'{plain.pid}\n')
        assert _select(port_file) == ''

        # ...and a sleeping server-shaped PID from the file is picked.
        sleeping = scene.add(_spawn_sleeping_uvicorn())
        pid_file.write_text(f'{sleeping.pid}\n')
        assert _select(port_file) == str(sleeping.pid)
    finally:
        pid_file.unlink(missing_ok=True)
        scene.close()


def test_default_path_is_unchanged(tmp_path):
    """D1: the non-scoped path still stops everyone on the port and
    still machine-reaps agents — proven with pgrep/pkill as recording
    no-ops, so the reap never signals anything real."""
    scene = _Scene()
    stub_dir = tmp_path / 'stub'
    stub_dir.mkdir()
    stub_log = tmp_path / 'stub.log'
    for name in ('pgrep', 'pkill'):
        stub = stub_dir / name
        stub.write_text(
            f'#!/bin/bash\necho "{name} $*" >> "{stub_log}"\nexit 1\n'
        )
        stub.chmod(0o755)

    try:
        port = _free_port()
        listener = scene.add(_spawn_foreign_listener(port))
        _wait_port_listening(port)
        client = scene.add(_spawn_client(port))
        decoy = scene.add(_spawn_agent_decoy())
        time.sleep(0.2)
        assert decoy.poll() is None

        env = _child_env()
        env['PATH'] = f'{stub_dir}{os.pathsep}{env.get("PATH", "")}'
        result = _run_stop(port, env=env)
        assert result.returncode == 0, result.stderr

        _wait_dead([listener, client])
        # The machine-wide agent reap still RUNS unchanged (its scan
        # pattern reaches pgrep); it is inert only because the stub is.
        scan = stub_log.read_text()
        assert 'pgrep -f' in scan and AGENT_PATTERN in scan
        # Unstubbed, that scan would kill this decoy — the stub is the
        # only reason it survives, which is the point of --scoped.
        assert decoy.poll() is None
    finally:
        scene.close()


def _select(port: int) -> str:
    """Call the factored selection exactly as stop.sh --scoped does."""
    out = subprocess.run(
        [
            'bash',
            '-c',
            f'. "{STOP_LIB}"; scoped_server_pids_for_port {port}',
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return out.stdout.strip()


def _pid_alive(pid: int) -> bool:
    """True when PID is a live process. A zombie (dead, not yet
    reaped) counts as dead — the kernel keeps its PID until the
    reaper collects it, so a naive kill -0 would lie."""
    out = subprocess.run(
        ['ps', '-o', 'state=', '-p', str(pid)],
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    return bool(out) and not out.startswith('Z')
