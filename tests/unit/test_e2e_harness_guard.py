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
   e2e modules do;
4. and the general form of the same rule, from the r8 inventory of
   app-wide singletons: no module running on the SHARED server may
   touch server-global state at all — the settings row, the vision key
   and catalog, a manual sync run, the catalog fanout, the shared
   knowledge tree, the ads binding of every store, a direct
   ``app_settings`` write. See
   :func:`test_no_shared_server_module_touches_a_server_singleton`.
"""

import ast
from contextlib import contextmanager
import importlib
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time

import pytest

from tests.e2e import e2e_helpers, test_catalog_sync
from tests.e2e.own_server import (
    OwnServer,
    assert_owned_port,
    pick_free_port,
    shared_port,
)

pytestmark = pytest.mark.unit

E2E_DIR = Path(__file__).resolve().parents[2] / 'tests' / 'e2e'
OWNED_HELPERS = {'own_server.py'}
# e2e_helpers.py IS the one definition of the shared address.
ADDRESS_HOME = 'e2e_helpers.py'
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


def test_every_fake_site_is_threaded():
    """A fake site the agent's browser opens must serve concurrently.

    Chrome preconnects — opens a socket and sends nothing yet. On a
    single-threaded ``HTTPServer`` that socket holds the only serving
    thread, so every later request hangs with zero bytes (observed: an
    agent reported a test's ad console as unreachable and its spend as
    zero). ``ThreadingHTTPServer`` (or a subclass of it) only.
    """
    offenders = []
    for path in sorted(E2E_DIR.rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Call):
                names.append(node.func)
            elif isinstance(node, ast.ClassDef):
                names.extend(node.bases)
            for n in names:
                name = (
                    n.attr
                    if isinstance(n, ast.Attribute)
                    else getattr(n, 'id', '')
                )
                if name == 'HTTPServer':
                    offenders.append(f'{path.name}:{node.lineno}')
    assert not offenders, (
        'single-threaded HTTPServer in an e2e fake site — use '
        'http.server.ThreadingHTTPServer:\n  ' + '\n  '.join(offenders)
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


def test_no_e2e_module_hardcodes_the_shared_log_name():
    """A server's log belongs to that server.

    ``start.sh`` used to truncate ONE shared ``logs/backend.log`` for
    every server started from the checkout, and a log-reading test
    then parsed a clobbered file. Logs are per-port now (the app's
    ``backend_<port>.log`` via ``server_log_path()``, and the shell's
    ``backend_stdout_<port>.log``); no test may name the old shared
    file.
    """
    offenders = []
    for path in sorted(E2E_DIR.rglob('*.py')):
        if 'backend.log' in path.read_text(encoding='utf-8'):
            offenders.append(str(path.relative_to(E2E_DIR)))
    assert not offenders, (
        'e2e modules must read the log of the server they talk to via '
        'tests/e2e/e2e_helpers.server_log_path(), never a shared '
        'backend.log:\n  ' + '\n  '.join(offenders)
    )


def test_start_sh_captures_stdout_per_port():
    """The shell redirect must be port-scoped too.

    uvicorn's stdout/stderr goes wherever start.sh points it; with a
    shared name every ``./start.sh <other port>`` truncated the first
    server's capture mid-run.
    """
    text = (E2E_DIR.parents[1] / 'start.sh').read_text(encoding='utf-8')
    assert 'backend_stdout_${PORT}.log' in text, (
        'start.sh must capture stdout to a per-port file'
    )
    for line in text.splitlines():
        if 'uvicorn app.main:app' in line and '>' in line:
            assert 'backend_stdout_${PORT}.log' in line, line
            assert 'backend.log' not in line, line


@contextmanager
def _helpers_under(env: dict):
    """Evaluate the shared-address helpers under *env*; restore after."""
    saved = {key: os.environ.get(key) for key in ('E2E_BASE_URL', 'E2E_PORT')}
    for key in saved:
        os.environ.pop(key, None)
    os.environ.update(env)
    try:
        helpers = importlib.import_module('tests.e2e.e2e_helpers')
        own = importlib.import_module('tests.e2e.own_server')
        importlib.reload(helpers)
        importlib.reload(own)
        yield helpers, own
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(importlib.import_module('tests.e2e.e2e_helpers'))
        importlib.reload(importlib.import_module('tests.e2e.own_server'))


def test_shared_address_defaults_agree():
    """Unset the vars and EVERY derivation must say 7777.

    The bug this pins: three fallbacks for one fact, two of them
    wrong — a reader went looking for backend_7811.log while the
    server wrote backend_7777.log. With a URL set, all of them must
    follow it.
    """
    with _helpers_under({}) as (helpers, own):
        assert helpers.SHARED_PORT == 7777
        assert own.shared_port() == 7777
        names = [p.name for p in helpers.server_log_candidates()]
        assert names and all(name == 'backend_7777.log' for name in names), (
            names
        )
    with _helpers_under({'E2E_BASE_URL': 'http://127.0.0.1:7899'}) as (
        helpers,
        own,
    ):
        assert helpers.SHARED_PORT == 7899
        assert own.shared_port() == 7899
        names = [p.name for p in helpers.server_log_candidates()]
        assert names and all(name == 'backend_7899.log' for name in names), (
            names
        )


_ADDRESS_TOKENS = (
    "os.environ.get('E2E_BASE_URL'",
    'os.environ.get("E2E_BASE_URL"',
    "os.environ.get('E2E_PORT'",
    'os.environ.get("E2E_PORT"',
    '127.0.0.1:7777',
    '127.0.0.1:7811',
    '7811',
)


def _without_docstrings(text: str) -> str:
    """*text* with every docstring's lines blanked out.

    Docstrings document usage (``VIBE_MCP_URL=http://127.0.0.1:7777``
    as an example of what an operator may pass); they define nothing a
    test reads, so the address guard must not count them.
    """
    lines = text.splitlines()
    doc_owners = (
        ast.Module,
        ast.ClassDef,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
    )
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, doc_owners) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            for i in range(first.lineno - 1, first.end_lineno):
                lines[i] = ''
    return '\n'.join(lines)


def _address_offenders(text: str) -> list:
    code = _without_docstrings(text)
    return [token for token in _ADDRESS_TOKENS if token in code]


def test_address_guard_counts_code_not_docstrings():
    """The guard's own contract, pinned both ways."""
    docstring_only = (
        '"""Usage: VIBE_MCP_URL=http://127.0.0.1:7777/api/mcp"""\n'
        'import os\n'
        "URL = os.environ.get('VIBE_MCP_URL', '')\n"
    )
    assert _address_offenders(docstring_only) == []
    fallback_in_code = '"""A module."""\nBASE = \'http://127.0.0.1:7777\'\n'
    assert _address_offenders(fallback_in_code) == ['127.0.0.1:7777']
    env_fallback = "import os\nPORT = os.environ.get('E2E_PORT', '7777')\n"
    assert _address_offenders(env_fallback) == ["os.environ.get('E2E_PORT'"]


def test_no_second_definition_of_the_shared_address():
    """The shared server's address lives ONLY in e2e_helpers.

    A fallback URL/port literal anywhere else is a second definition
    waiting to disagree. Docstrings that show an example command are
    not definitions and are not scanned (``_without_docstrings``).
    """
    offenders = []
    for path in sorted(E2E_DIR.rglob('*.py')):
        if path.name == ADDRESS_HOME or path.name == '__init__.py':
            continue
        text = path.read_text(encoding='utf-8')
        for token in _address_offenders(text):
            offenders.append(f'{path.relative_to(E2E_DIR)}: {token}')
    assert not offenders, (
        'the shared server address is defined once, in '
        'tests/e2e/e2e_helpers.py:\n  ' + '\n  '.join(offenders)
    )


def test_no_module_level_server_log_resolution():
    """Resolve logs where they are READ, never at import.

    ``server_log_path()`` raises when no log exists — fine inside one
    test (it fails, with the candidate list), fatal at module scope
    (it breaks COLLECTION of the whole module). Call it from a
    function or not at all.
    """

    class ModuleLevelCalls(ast.NodeVisitor):
        def __init__(self):
            self.hits = 0

        def visit_FunctionDef(self, node):
            pass

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            pass

        def visit_Call(self, node):
            func = node.func
            name = getattr(func, 'id', None) or getattr(func, 'attr', None)
            if name == 'server_log_path':
                self.hits += 1
            self.generic_visit(node)

    offenders = []
    for path in sorted(E2E_DIR.rglob('*.py')):
        if path.name == ADDRESS_HOME or path.name == '__init__.py':
            continue
        visitor = ModuleLevelCalls()
        visitor.visit(ast.parse(path.read_text(encoding='utf-8')))
        if visitor.hits:
            offenders.append(str(path.relative_to(E2E_DIR)))
    assert not offenders, (
        'resolve the server log inside the test that reads it, not at '
        'module level:\n  ' + '\n  '.join(offenders)
    )


def test_own_server_log_is_a_fact_not_a_search():
    """The owner of a server knows its log path before it starts.

    ``OwnServer`` runs ``start.sh`` itself, so its log path is
    deterministic from the moment it picks the port — reading
    ``.log`` must never search (or raise) and must not touch the
    filesystem. A searcher (``server_log_candidates``) is for the
    SHARED server only, whose launch the test process cannot see.
    """
    before = set(Path.cwd().iterdir())
    server = OwnServer(port=4242, home=Path('/tmp/unused'))
    resolved = server.log
    after = set(Path.cwd().iterdir())
    assert before == after, 'reading .log must not touch the filesystem'
    assert resolved == E2E_DIR.parents[1] / 'logs' / 'backend_4242.log'
    assert resolved.name == 'backend_4242.log'


def test_start_sh_still_forces_its_logs_dir():
    """The launcher and the helper must not drift.

    ``start_sh_log_path`` bakes in "start.sh exports LOG_DIR to the
    repo's logs/"; if start.sh ever stops doing that, this fails.
    """
    text = (E2E_DIR.parents[1] / 'start.sh').read_text(encoding='utf-8')
    assert 'LOG_DIR="$SCRIPT_DIR/logs"' in text


def test_no_skip_on_a_missing_server_log():
    """A missing server log must FAIL an assertion, never skip it.

    The bug this guards (Brief #17b): ``test_two_browser_tasks_
    both_complete`` reported SKIPPED after ~5 minutes of real work in
    every CI run — its helper did ``if not log_path.exists():
    pytest.skip(...)`` against a name no CI server writes, so its core
    concurrency assertion never ran once. The helpers now resolve the
    server's own log through ``e2e_helpers.server_log_path()`` (which
    raises when absent). Any ``if not <x>.exists(): pytest.skip`` in an
    e2e module is that pattern returning — forbid it.
    """
    root = Path(__file__).resolve().parents[1] / 'e2e'
    bad = []
    for path in sorted(root.glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.If):
                    continue
                has_not = any(
                    isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not)
                    for n in ast.walk(node.test)
                )
                has_exists = any(
                    isinstance(n, ast.Attribute) and n.attr == 'exists'
                    for n in ast.walk(node.test)
                )
                if not (has_not and has_exists):
                    continue
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == 'skip'
                    ):
                        bad.append(f'{path.name}:{sub.lineno}')
    assert not bad, (
        'a missing file (a server log in particular) must FAIL, not '
        f'skip: {bad}'
    )


def _start_sh_build_block() -> str:
    """start.sh's frontend-build block, exactly as written."""
    text = (E2E_DIR.parents[1] / 'start.sh').read_text(encoding='utf-8')
    m = re.search(r'^FRONTEND_DIR=.*?^fi\n', text, re.M | re.S)
    assert m, 'start.sh frontend build block not found'
    return m.group(0)


def _builds(tmp_path: Path, env: dict) -> bool:
    """Run the block against a fake checkout; did it call ``pnpm build``?"""
    (tmp_path / 'frontend' / 'node_modules').mkdir(parents=True)
    (tmp_path / 'frontend' / 'package.json').write_text('{}')
    calls = tmp_path / 'pnpm.calls'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    pnpm = fake_bin / 'pnpm'
    pnpm.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    pnpm.chmod(0o755)
    subprocess.run(
        ['bash', '-c', _start_sh_build_block()],
        env={
            'PATH': f'{fake_bin}:/usr/bin:/bin',
            'SCRIPT_DIR': str(tmp_path),
            **env,
        },
        check=True,
        capture_output=True,
    )
    return calls.exists() and 'build' in calls.read_text()


def test_an_owned_server_never_rebuilds_the_shared_frontend(
    tmp_path, monkeypatch
):
    """A test's own server must not empty the frontend the shared one serves.

    Observed in CI: ``pnpm build`` (run by ``start.sh``) empties
    ``frontend/dist`` before writing it; a sync e2e restarted its own
    server on the same checkout and a UI test on another worker loaded
    ``/`` from the shared server inside that window — 404.
    """
    assert _builds(tmp_path / 'plain', {})
    assert not _builds(tmp_path / 'skip', {'VIBE_SKIP_FRONTEND_BUILD': '1'})

    seen = {}

    def fake_run(argv, env=None, **_):
        seen.update(env or {})
        return subprocess.CompletedProcess(argv, 0, '', '')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    monkeypatch.setattr(OwnServer, 'wait_health', lambda self: None)
    server = OwnServer(port=4242, home=tmp_path / 'home')
    server.start(env={'VIBE_SKIP_FRONTEND_BUILD': ''})
    assert seen.get('VIBE_SKIP_FRONTEND_BUILD') == '1'


def test_the_catalog_sync_is_never_driven_on_the_shared_server():
    """The global catalog sync has one driver per server: its owner.

    Observed in CI: a catalog test on one worker added a knowledge file
    and synced while the knowledge-cleanup test on another sat between
    its two syncs, so the store it expected to be skipped was rebuilt.
    The first answer was a cross-worker flock around the shared sync
    (``ca1dfb3``). A lock makes the collision rare, not impossible — it
    serialises two drivers of ONE tree; it never separates the trees.
    The design fix is ownership: the fanout rebuilds EVERY store's
    catalog on the server it runs on, so a test that drives it drives it
    on a server of its own and the shared one is refused outright. Same
    meaning as the lock — never drive a sync another worker can see —
    now enforced by naming the server instead of waiting for it.
    """
    shared = f'http://127.0.0.1:{e2e_helpers.SHARED_PORT}'
    with pytest.raises(AssertionError, match='server it owns'):
        test_catalog_sync.trigger_catalog_sync(None, set(), shared)

    # Any other port is an owned one, and gets through the refusal.
    owned = f'http://127.0.0.1:{e2e_helpers.SHARED_PORT + 1}'
    assert e2e_helpers._port_of(owned) != e2e_helpers.SHARED_PORT


def test_point_at_server_refuses_the_shared_server():
    """Re-aiming the helpers is not a way back onto the shared server.

    ``point_at_server`` swaps the module globals every e2e helper reads,
    so pointing it at the shared server would LOOK like ownership while
    the module still raced every other worker's state.
    """
    shared = f'http://127.0.0.1:{e2e_helpers.SHARED_PORT}'
    with pytest.raises(ValueError, match='SHARED'):
        with e2e_helpers.point_at_server(shared):
            pass


#: Server-global state: one row, one file or one in-memory singleton
#: behind an endpoint, on the server EVERY xdist worker runs its tests
#: against. A test that writes it changes another worker's running
#: tests, and a test that reads it can be answered with another
#: worker's write — so a module that touches one of these owns its
#: server (``tests/e2e/own_server.py``) instead.
#:
#: This is the inventory Brief r8 item 1 took from reading every
#: ``tests/e2e/test_*.py``, now expressed as code: prose in a docstring
#: ("this needs the server started with X") is what let the catalog
#: tests, the settings switch and the ads binding all collide anyway.
SINGLETON_SURFACES = {
    'the server-wide settings row (auth switch, agent slots)': (
        '/api/settings',
    ),
    'the one vision key + priced catalog': ('/api/vision/config',),
    'a manual run of a global sync job': ('/api/sync/',),
    'the catalog sync fanout over every store': ('SYSTEM_CATALOG_SYNC_ID',),
    'the ads binding of every store row': ('/api/ads/stores/sync',),
    'the shared knowledge tree': ("VIBE_SELLER_DIR / 'knowledge'",),
    'a direct write into app_settings': ('INTO app_settings',),
}

OWNERSHIP_TOKENS = ('owned_agent_stack', 'OwnServer(')


def _skips_unless_mock_cli(tree: ast.Module) -> bool:
    """True when the module refuses to run at all without ``MOCK_CLI``.

    A ``if not MOCK_CLI: pytest.skip(..., allow_module_level=True)`` at
    module level means the module only ever runs in the ``e2e-mock-cli``
    CI lane — ONE pytest process, no xdist (pinned below) — so there is
    no second worker to race on that lane's shared server. The gate must
    be module-level and on MOCK_CLI being ABSENT: a module that skips
    when it is present (``test_auto_memory_off``) runs in the parallel
    lane and gets no exemption.
    """
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        if 'MOCK_CLI' not in ast.dump(node.test):
            continue
        if any(isinstance(n, ast.UnaryOp) for n in ast.walk(node.test)):
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and getattr(sub.func, 'attr', '') == 'skip'
                    and any(
                        kw.arg == 'allow_module_level' for kw in sub.keywords
                    )
                ):
                    return True
    return False


def _owns_its_server(text: str) -> bool:
    return any(token in text for token in OWNERSHIP_TOKENS)


def test_no_shared_server_module_touches_a_server_singleton():
    """Global state means an owned server — or the module never runs.

    The failure this pins is the class the r8 inventory found: two
    workers driving one catalog fanout, one module flipping
    ``auth_required`` while another asserts a 401, one rewriting the
    vision key another test reads back. Nothing here can be caught at
    runtime by the test that caused it — it shows up as an unrelated
    test failing on a different worker — so it is caught statically.
    """
    offenders = []
    exempt: list[str] = []
    for path in sorted(E2E_DIR.glob('test_*.py')):
        text = path.read_text(encoding='utf-8')
        if _owns_its_server(text):
            continue
        tree = ast.parse(text)
        surfaces = sorted(
            surface
            for surface, tokens in SINGLETON_SURFACES.items()
            if any(token in text for token in tokens)
        )
        if not surfaces:
            continue
        if _skips_unless_mock_cli(tree):
            exempt.append(f'{path.name}: {", ".join(surfaces)}')
            continue
        offenders.append(f'{path.name}: {", ".join(surfaces)}')
    assert not offenders, (
        'these modules touch SERVER-GLOBAL state on the shared e2e '
        'server, where another xdist worker can change or observe it — '
        'give them their own server via '
        'tests/e2e/own_server.owned_agent_stack:\n  '
        + '\n  '.join(offenders)
        + '\n(serial-lane exempt, gated on MOCK_CLI: '
        + '; '.join(exempt)
        + ')'
    )


def test_the_serial_lane_stay_serial():
    """The MOCK_CLI exemption is only true while that lane has no -n.

    If the e2e-mock-cli job ever runs xdist, the exempted modules DO
    have a second worker to race, and the exemption above must stop
    applying to them.
    """
    ci = (E2E_DIR.parents[1] / '.github' / 'workflows' / 'ci.yml').read_text(
        encoding='utf-8'
    )
    step = ci.split('- name: Run mock CLI E2E tests', 1)[1].split(
        '- name: Stop mock CLI server', 1
    )[0]
    assert ' -n ' not in step and '--dist' not in step, (
        'the mock-CLI lane now runs xdist — the MOCK_CLI exemption in '
        'test_no_shared_server_module_touches_a_server_singleton no '
        'longer means "no other worker": give those modules their own '
        'servers'
    )


def test_e2e_helpers_import_without_posix_only_modules():
    """The Windows installer smoke loads the e2e conftest, which imports
    e2e_helpers; a top-level ``import fcntl`` (absent on Windows) failed
    that job before any test ran."""
    code = (
        'import sys\n'
        'sys.modules["fcntl"] = None\n'
        'import tests.e2e.e2e_helpers\n'
    )
    done = subprocess.run(
        [sys.executable, '-c', code],
        cwd=E2E_DIR.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr


def test_stopping_an_owned_server_frees_its_port(tmp_path, monkeypatch):
    """Stopped means the port is free, or start.sh refuses the restart.

    Observed in CI: the pid file holds ``uv run``; its uvicorn child kept
    listening through its shutdown, and a restart (no frontend build in
    between any more) found the port busy — "did not come up".
    """
    port = pick_free_port()
    child = (
        'import signal, socket, sys, time\n'
        's = socket.socket(); s.setsockopt(socket.SOL_SOCKET, '
        'socket.SO_REUSEADDR, 1)\n'
        f's.bind(("127.0.0.1", {port})); s.listen()\n'
        'signal.signal(signal.SIGTERM, lambda *_: (time.sleep(1.5), '
        'sys.exit(0)))\n'
        'while True: time.sleep(0.1)\n'
    )
    parent = subprocess.Popen(
        ['bash', '-c', f"{sys.executable} -c '{child}' & wait"],
    )
    # Reap it as its real parent would (otherwise a zombie pid reads as
    # alive and stop() waits on that instead of the port).
    threading.Thread(target=parent.wait, daemon=True).start()
    deadline = time.time() + 10
    while time.time() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                break
        time.sleep(0.1)
    monkeypatch.chdir(tmp_path)
    (tmp_path / f'.pids_{port}').write_text(str(parent.pid))
    server = OwnServer(port=port, home=tmp_path / 'home')
    try:
        server.stop()
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', port)) != 0, (
                'stop() returned while the port was still listened on'
            )
    finally:
        parent.kill()
        subprocess.run(['pkill', '-P', str(parent.pid)], check=False)
