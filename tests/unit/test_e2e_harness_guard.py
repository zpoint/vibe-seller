"""No e2e test may restart or stop the SHARED server.

The bug this guards: an e2e test restarted the shared server mid-tier
(``kill -TERM`` on ``.pids_<PORT>`` + ``./start.sh``), and every other
worker's in-flight task failed with ``Server restarted while task was
active``. A test that needs restarts owns its server instead
(``tests/e2e/own_server.py``), and two checks keep it that way:

1. at runtime, ``own_server`` refuses the shared server's port;
2. here, no ``tests/e2e`` module outside ``own_server.py`` is allowed
   to reach for the server-control surface itself (``stop.sh``, the
   ``.pids_`` files, a ``'./start.sh'`` invocation). Prose that merely
   mentions how to start a server by hand is fine — control-surface
   usage is not.
"""

from pathlib import Path

import pytest

from tests.e2e.own_server import assert_owned_port, shared_port

pytestmark = pytest.mark.unit

E2E_DIR = Path(__file__).resolve().parents[2] / 'tests' / 'e2e'
OWNED_HELPERS = {'own_server.py'}
FORBIDDEN_TOKENS = ('stop.sh', '.pids_', "'./start.sh", '"./start.sh')


def test_the_shared_port_is_refused_at_runtime():
    with pytest.raises(ValueError, match='SHARED'):
        assert_owned_port(shared_port())
    # anything else is fine
    assert assert_owned_port(shared_port() + 1) == shared_port() + 1


def test_no_e2e_module_reaches_for_the_server_control_surface():
    offenders = []
    for path in sorted(E2E_DIR.rglob('*.py')):
        if path.name in OWNED_HELPERS or path.name == '__init__.py':
            continue
        text = path.read_text(encoding='utf-8')
        for token in FORBIDDEN_TOKENS:
            if token in text:
                offenders.append(f'{path.relative_to(E2E_DIR)}: {token}')
    assert not offenders, (
        'e2e modules must not start/stop servers themselves — use '
        'tests/e2e/own_server.py, which refuses the shared port:\n  '
        + '\n  '.join(offenders)
    )
