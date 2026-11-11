"""A server an e2e test OWNS: started, restarted and stopped by the test.

The shared e2e server (``E2E_BASE_URL``, started once by the harness)
serves every other test in the tier — restarting it fails every other
worker's in-flight tasks (``Server restarted while task was active``,
written by the queue's startup recovery). A test that needs to restart
"its" server must therefore OWN one: a dedicated free port and a
dedicated ``VIBE_HOME``, started and stopped here.

The ownership is enforced, not documented: :func:`assert_owned_port`
refuses the shared server's port outright, so no caller — including
the helpers below — can ever stop or restart it.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import time

import httpx

from tests.e2e import e2e_helpers
from tests.e2e.e2e_helpers import SHARED_PORT, start_sh_log_path


def shared_port() -> int:
    """The port the shared e2e server runs on.

    One definition, in ``e2e_helpers.SHARED_PORT`` — a second default
    here is how a server ends up "owning" the wrong port.
    """
    return SHARED_PORT


def assert_owned_port(port: int) -> int:
    """Refuse to touch the shared server. This is the guard."""
    if int(port) == shared_port():
        raise ValueError(
            f'port {port} is the SHARED e2e server — a test must own its '
            'server (dedicated port + dedicated VIBE_HOME) and never '
            'restart or stop the shared one'
        )
    return int(port)


def _listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def pick_free_port() -> int:
    """A port nothing is listening on right now."""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


#: For the e2e that test the sync jobs themselves: their server reads a
#: fake GitHub and fake services, so the outside sources are on even when
#: the surrounding run (CI) turns them off for every other server.
SYNC_SWITCHES_ON = {'VIBE_GITHUB_MAIN_SYNC': 'on', 'VIBE_EXTERNAL_SYNC': 'on'}

#: What a test-owned server needs to run REAL AGENTS like the shared CI
#: server does. The pytest step's environment is not the server step's:
#: CI sets ``MAX_AGENT_CONCURRENCY`` (and the sync-off / telemetry-off
#: switches) when it STARTS the shared server, so an owned server started
#: from the test step would silently run with the app's default 2 agent
#: slots — a 12-task catalog fanout would queue instead of fan out — and
#: would pull skills/knowledge from GitHub main and phone home mid-run.
#: Same choice ci.yml makes for the shared server ("the tree under test is
#: this PR, not main"); the sync e2e opt back in with SYNC_SWITCHES_ON.
#: An explicit ``env`` on the owned server still wins over this.
AGENT_SERVER_ENV = {
    'MAX_AGENT_CONCURRENCY': os.environ.get('MAX_AGENT_CONCURRENCY', '16'),
    'VIBE_GITHUB_MAIN_SYNC': 'off',
    'VIBE_EXTERNAL_SYNC': 'off',
    'VIBE_SELLER_TELEMETRY': '0',
}


@contextmanager
def owned_agent_stack(tmp_path_factory, name: str, env: dict | None = None):
    """One server this MODULE owns, with every helper pointed at it.

    The whole owned-stack ritual in one place, because a test that
    touches server-global state must do ALL of it to leave the shared
    server behind, and a half-done move is worse than none:

      * a dedicated free port and a dedicated temp ``VIBE_HOME`` — so
        its catalogs, its settings rows, its ``.claude`` memory dir and
        its agent venv are nobody else's;
      * ``AGENT_SERVER_ENV`` — the shared server's agent capacity and
        its no-outside-calls switches;
      * ``e2e_helpers.point_at_server`` — the helpers, the worker's
        provider profile and an SSE auto-answer for THIS server.

    Use it from a module-scoped fixture (``scope='module'`` runs the
    setup once PER WORKER: if xdist splits the module's tests over two
    workers, each gets its own server and its own home — which is the
    point of owning one).
    """
    home = tmp_path_factory.mktemp(name)
    server = OwnServer(
        port=pick_free_port(),
        home=home,
        env={**AGENT_SERVER_ENV, **(env or {})},
    )
    server.start()
    try:
        with e2e_helpers.point_at_server(server.base_url):
            yield server
    finally:
        server.stop()


#: Every server THIS process started, kept for the session-end leak
#: check (:func:`assert_no_owned_server_leaked`). A per-process list is
#: exactly right under xdist: each worker owns the servers it started,
#: and the worker that leaked one is the one that says so.
_STARTED: list['OwnServer'] = []


def assert_no_owned_server_leaked() -> None:
    """Fail loudly if a server this worker started is still around.

    A server that outlives the run holds its port, keeps writing to its
    ``logs/backend_<port>.log``, and leaves ``.pids_<port>`` behind for
    the next ``./stop.sh`` — or the next test session — to act on. The
    module fixtures stop their servers today, so this is not a fix for a
    leak seen; it is the check that keeps a future fixture that forgets
    ``stop()`` from silently becoming one, since the symptom shows up
    much later and somewhere else.

    It only ever LOOKS (a connect probe and a file check): it never
    signals a process, and the shared server's port is refused by
    :func:`assert_owned_port` on the way in, so there is nothing here
    that could touch it.
    """
    leaked = []
    for server in _STARTED:
        if int(server.port) == shared_port():
            # Not ours to police, and it is SUPPOSED to outlive the
            # run: counting it would fail every session of the tier.
            continue
        holding = []
        if _listening(server.port):
            holding.append('still listening')
        if server._pidfile.exists():
            holding.append(f'{server._pidfile} still there')
        if holding:
            leaked.append(f'{server.base_url} ({", ".join(holding)})')
    if leaked:
        raise AssertionError(
            'owned e2e servers outlived the run — a module fixture '
            'started them and never stopped them: ' + '; '.join(leaked)
        )
    _STARTED.clear()


class OwnServer:
    """One test-owned server: its own port, its own home, its own pid."""

    def __init__(self, port: int, home: Path, env: dict | None = None):
        self.port = assert_owned_port(port)
        self.home = Path(home)
        self.extra_env = dict(env or {})

    @property
    def base_url(self) -> str:
        return f'http://127.0.0.1:{self.port}'

    @property
    def log(self) -> Path:
        """Where THIS server's app log will be — a fact, not a search.

        ``OwnServer`` runs ``start.sh`` itself, and ``start.sh``
        forces ``LOG_DIR`` to the repo's ``logs/`` before launching
        uvicorn — so the path is deterministic from the moment the
        port is picked, and valid BEFORE ``start()`` (the fixture
        needs it to point readers at the log the run is about to
        create). Only a non-owner of a server searches for its log
        (``e2e_helpers.server_log_candidates``).
        """
        return start_sh_log_path(self.port)

    @property
    def _pidfile(self) -> Path:
        return Path(f'.pids_{self.port}')

    def _stop_own(self) -> None:
        """SIGTERM this server (and its uvicorn child) — nobody else's."""
        pidfile = self._pidfile
        if not pidfile.exists():
            return
        pid = int(pidfile.read_text().strip() or 0)
        if pid:
            kids = subprocess.run(
                ['pgrep', '-P', str(pid)], capture_output=True, text=True
            ).stdout.split()
            os.kill(pid, 15)
            for kid in kids:
                try:
                    os.kill(int(kid), 15)
                except (ProcessLookupError, ValueError):
                    pass
            for _ in range(30):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(1)
        pidfile.unlink(missing_ok=True)
        # Stopped means the PORT is free: the pid file holds `uv run`,
        # whose uvicorn child still listens through its shutdown, and
        # start.sh refuses a port something listens on (observed: a
        # restart with no frontend build in between found it busy).
        deadline = time.time() + 30
        while _listening(self.port):
            if time.time() > deadline:
                raise AssertionError(
                    f'port {self.port} still listened on 30s after stopping '
                    'this server'
                )
            time.sleep(0.2)

    def start(self, env: dict | None = None) -> None:
        """(Re)start this server with its home and any env additions."""
        assert_owned_port(self.port)
        self._stop_own()
        self.home.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(
            ['./start.sh', str(self.port)],
            env={
                **os.environ,
                'VIBE_HOME': str(self.home),
                **self.extra_env,
                **(env or {}),
                # Rebuilding would empty the frontend the shared server
                # serves from this same checkout (observed: a UI test's
                # page load got 404 during a restart here).
                'VIBE_SKIP_FRONTEND_BUILD': '1',
            },
            check=False,
            capture_output=True,
            text=True,
        )
        if done.returncode != 0:
            raise AssertionError(
                f'start.sh {self.port} failed ({done.returncode}):\n'
                f'{(done.stdout or "")[-1500:]}{(done.stderr or "")[-1500:]}'
            )
        self.wait_health()
        # Booked as soon as it is up: the session-end check reports the
        # servers this worker started, whether or not their fixture got
        # as far as calling stop().
        _STARTED.append(self)

    def wait_health(self, timeout: float = 60) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if (
                    httpx.get(
                        f'{self.base_url}/api/health', timeout=2
                    ).status_code
                    == 200
                ):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise AssertionError(
            f'server on {self.base_url} (VIBE_HOME={self.home}) did not come up'
        )

    def stop(self) -> None:
        """Stop this server — only ever its own pid file."""
        assert_owned_port(self.port)
        self._stop_own()
