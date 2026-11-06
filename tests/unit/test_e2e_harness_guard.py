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
   usage is not;
3. a test that needs a NON-DEFAULT server env (the sync knobs) cannot
   silently run against the shared server: a module may not read
   ``E2E_BASE_URL`` while also requiring ``VIBE_SYNC_*`` — such a test
   must own its server (``tests/e2e/own_server.py``) like the two sync
   e2e modules do.
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


def test_no_module_mixes_the_shared_base_url_with_server_sync_env():
    """Non-default server env means a test must OWN its server.

    The shared CI server runs with default sync cadences and the real
    GitHub URLs; a test that needs ``VIBE_SYNC_*`` overrides (or the
    fake-repo URLs) and still reads ``E2E_BASE_URL`` is silently
    testing the wrong server. Both sync e2e modules own theirs.
    """
    offenders = []
    for path in sorted(E2E_DIR.rglob('*.py')):
        if path.name in OWNED_HELPERS or path.name == '__init__.py':
            continue
        text = path.read_text(encoding='utf-8')
        if 'E2E_BASE_URL' in text and 'VIBE_SYNC_' in text:
            offenders.append(str(path.relative_to(E2E_DIR)))
    assert not offenders, (
        'these modules configure server sync env but read the shared '
        'E2E_BASE_URL — give them their own server via '
        'tests/e2e/own_server.py:\n  ' + '\n  '.join(offenders)
    )
