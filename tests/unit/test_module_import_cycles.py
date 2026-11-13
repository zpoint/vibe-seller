"""Every module the update feature added must import on its own.

Why this exists rather than a passing `import app.auto_update` in some
other file: the real server starts from `app.main`, but an installed
build hit

    ImportError: cannot import name 'async_session' from partially
    initialized module 'app.database'

because a new module imported ``app.database`` at module top, and
``app.database`` → ``app.knowledge_contract`` → ``app.ai`` → … →
``app.sync.state`` → ``app.database`` is a cycle that only survives when
``app.database`` is finished before the last link runs.

The test suite cannot see that. ``tests/conftest.py`` imports ``app.main``
first, which resolves the cycle before any test asks, so every unit and
workflow test stayed green while the shipped product could not start. The
only harness that caught it was building a wheel and running it.

So: import each module in a FRESH interpreter, alone, the way the
packaged server does. A module that needs something else loaded first
fails here, where it is a one-line fix, instead of on a user's machine as
a server that never comes up.
"""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

#: The modules this feature added, plus the ones it now imports.
MODULES = [
    'app.bundle',
    'app.update_idle',
    'app.update_events',
    'app.pip_update',
    'app.pip_apply_helper',
    'app.auto_update',
    'app.windows_update',
    'app.upgrade_restart',
    'app.release_channel',
    'app.update_check',
    'app.routers.system',
    'app.apply_launch',
]


@pytest.mark.parametrize('module', MODULES)
def test_module_imports_alone_in_a_fresh_interpreter(module, tmp_path):
    """`-I`: no cwd and no user site-packages, so the import answers for
    the module itself rather than for whatever a checkout happens to put
    on sys.path first."""
    result = subprocess.run(  # noqa: S603 — a real interpreter is the point
        [sys.executable, '-I', '-c', f'import {module}'],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f'{module} cannot be imported on its own:\n{result.stderr[-2000:]}'
    )


def test_the_whole_app_package_still_starts_from_its_entry_point():
    """The chain a wheel runs: `app.cli` → the server module."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-I', '-c', 'import app.main, app.cli'],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_a_top_level_database_import_is_the_thing_being_guarded(tmp_path):
    """The guard must be able to fail, or it guards nothing.

    Reproducing the actual bug in a throwaway module proves the test
    above is the reason `app/update_idle.py` imports ``app.database``
    inside the function, and not by style preference.
    """
    src = tmp_path / 'victim'
    (src / 'app').mkdir(parents=True)
    (src / 'app' / '__init__.py').write_text('', encoding='utf-8')
    # Same shape as the real cycle: A imports B at top, B imports A.
    (src / 'app' / 'a.py').write_text(
        'from app import b\nVALUE = 1\n', encoding='utf-8'
    )
    (src / 'app' / 'b.py').write_text(
        'from app.a import VALUE\n', encoding='utf-8'
    )
    # `-I` would ignore PYTHONPATH, which is the whole delivery here;
    # `-s` keeps the user's site-packages out, which is the only part of
    # isolation this demonstration needs.
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-s', '-c', 'import app.a'],
        cwd=str(src),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0, result.stderr
    assert 'partially initialized' in result.stderr, result.stderr
