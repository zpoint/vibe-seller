"""start.sh refuses a port only when a server LISTENS on it.

Observed: a local proxy kept a dead client connection to a test server's
port after the server stopped; ``lsof -i:PORT`` matched that client
socket and start.sh refused a port nothing listened on. The check runs
here as written in start.sh, against a real listener and a real
leftover client socket.
"""

from pathlib import Path
import re
import shutil
import socket
import subprocess

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(not shutil.which('lsof'), reason='needs lsof'),
]

START_SH = Path(__file__).resolve().parents[2] / 'start.sh'


def _check_line() -> str:
    m = re.search(r'^if (lsof [^;]*); then', START_SH.read_text(), re.M)
    assert m, 'start.sh port check not found'
    return m.group(1)


def _in_use(port: int) -> bool:
    cmd = _check_line().replace('"$PORT"', str(port))
    return subprocess.run(['bash', '-c', cmd], check=False).returncode == 0


def test_a_listener_makes_the_port_busy():
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen()
    try:
        assert _in_use(srv.getsockname()[1])
    finally:
        srv.close()


def test_a_leftover_client_connection_does_not():
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen()
    port = srv.getsockname()[1]
    client = socket.create_connection(('127.0.0.1', port))
    conn, _ = srv.accept()
    conn.close()
    srv.close()  # the server is gone; the client socket lingers
    try:
        assert not _in_use(port)
    finally:
        client.close()
