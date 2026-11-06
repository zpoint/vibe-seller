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

import os
from pathlib import Path
import socket
import subprocess
import time

import httpx


def shared_port() -> int:
    """The port the shared e2e server runs on."""
    base = os.environ.get('E2E_BASE_URL', 'http://127.0.0.1:7811')
    return int(os.environ.get('E2E_PORT') or base.rsplit(':', 1)[-1] or 7811)


def assert_owned_port(port: int) -> int:
    """Refuse to touch the shared server. This is the guard."""
    if int(port) == shared_port():
        raise ValueError(
            f'port {port} is the SHARED e2e server — a test must own its '
            'server (dedicated port + dedicated VIBE_HOME) and never '
            'restart or stop the shared one'
        )
    return int(port)


def pick_free_port() -> int:
    """A port nothing is listening on right now."""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


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
        """Where ``start.sh`` puts this server's log.

        ``start.sh`` exports ``LOG_DIR`` to its own ``logs/`` before
        launching uvicorn (the app logs to ``backend_<port>.log``
        there), whatever ``VIBE_HOME`` says — so the log lives beside
        the repo, not in the temp home.
        """
        return Path('logs') / f'backend_{self.port}.log'

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

    def start(self, env: dict | None = None) -> None:
        """(Re)start this server with its home and any env additions."""
        assert_owned_port(self.port)
        self._stop_own()
        self.home.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ['./start.sh', str(self.port)],
            env={
                **os.environ,
                'VIBE_HOME': str(self.home),
                **self.extra_env,
                **(env or {}),
            },
            check=False,
            capture_output=True,
        )
        self.wait_health()

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
