"""Single-install behaviour: the default-home paths are UNCHANGED.

Every customer runs ONE install (the default home). #350 resolves the
store download dir instead of spelling it — on a default install that
must be BYTE-EQUAL to what the parent commit computed, or the change
is not invisible. This test pins, for a sandboxed default home
(``VIBE_HOME`` unset, ``HOME``/``USERPROFILE`` pointed at a temp dir):

  * ``VIBE_STORE_DOWNLOADS`` (the agent launch env + the wrapper
    export — one helper, ``store_downloads_dir``);
  * the download dir the store context spells;
  * the mux pin (``prepare_download_dir``).

against the parent's formula ``~/.vibe-seller/downloads/<slug>``
expanded in that same sandbox home. The formula literal is allowed
only because it IS the parent's formula; the parent-code test below
proves that against origin/main's actual ``app.config`` where git can
reach it (locally; CI's shallow PR checkout cannot, so it skips
there). Negative control: divert ``Path.home`` and the equality
breaks.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from app.config import BASE_DIR

SLUG = 'acme-shop'

# The subprocess script: computes every surface's value from THIS
# branch's code, with the sandbox home in its environment.
_PROBE = (
    textwrap.dedent(
        """
    import json
    import re
    import tempfile
    from pathlib import Path
    from unittest import mock

    from app.browser import wrapper as wrapper_mod
    from app.browser.downloads import prepare_download_dir
    from app.browser.downloads import store_downloads_dir
    from app.models.store import Store
    from app.task_runner_context import build_store_context

    slug = %r
    out = {}
    out['env_var'] = store_downloads_dir(slug)
    out['mux_pin'] = str(prepare_download_dir(slug))
    bin_dir = Path(tempfile.mkdtemp())
    with mock.patch.object(wrapper_mod, '_BIN_DIR', bin_dir):
        wrapper_mod.write_browser_use_wrapper(
            'Acme', 'chrome', 9222, store_id='s1', slug=slug
        )
    text = (bin_dir / slug / 'browser-use').read_text(encoding='utf-8')
    m = re.search(r'export VIBE_STORE_DOWNLOADS="([^"]+)"', text)
    out['wrapper_export'] = m.group(1)
    ctx = build_store_context(
        Store(id='s', name='Acme', slug=slug, browser_backend='chrome')
    )
    m = re.search(r'browser downloads land in `([^`]+)`', ctx)
    out['store_context'] = m.group(1).rstrip('/').rstrip('\\\\')
    print(json.dumps(out))
    """
    )
    % SLUG
)


def _sandbox_env(home: Path) -> dict:
    """A fresh default-home environment: VIBE_HOME unset, HOME (and
    the Windows spellings) pointed at the sandbox dir."""
    env = dict(os.environ)
    env.pop('VIBE_HOME', None)
    env.pop('VIBE_DEMO_MODE', None)
    env['HOME'] = str(home)
    # Windows: Path.home() resolves through USERPROFILE (with
    # HOMEDRIVE/HOMEPATH as fallbacks) — pin all of them.
    env['USERPROFILE'] = str(home)
    env.pop('HOMEDRIVE', None)
    env.pop('HOMEPATH', None)
    return env


def _run_probe(home: Path, extra_pythonpath: Path | None = None) -> dict:
    env = _sandbox_env(home)
    if extra_pythonpath is not None:
        env['PYTHONPATH'] = (
            f'{extra_pythonpath}{os.pathsep}{env.get("PYTHONPATH", "")}'
        )
    r = subprocess.run(
        [sys.executable, '-c', _PROBE],
        cwd=str(BASE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.unit
def test_default_home_values_equal_the_parents_formula(tmp_path: Path):
    """Every surface names the same dir the parent computed, byte for
    byte, in a sandboxed default home."""
    home = tmp_path / 'home'
    home.mkdir()
    values = _run_probe(home)
    expected = str(home / '.vibe-seller' / 'downloads' / SLUG)
    for key in ('env_var', 'mux_pin', 'wrapper_export', 'store_context'):
        assert values[key] == expected, (key, values[key], expected)


@pytest.mark.unit
def test_parent_code_agrees_with_the_formula(tmp_path: Path):
    """The literal above IS the parent's formula: run origin/main's
    own ``app.config`` in the same sandbox home and compare.

    Skips where git cannot read origin/main (CI's shallow PR
    checkout); the formula equality above runs everywhere.
    """
    r = subprocess.run(
        ['git', 'show', 'origin/main:app/config.py'],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        pytest.skip('origin/main not available (shallow checkout)')
    parent_dir = tmp_path / 'parent'
    parent_dir.mkdir()
    # A minimal package context so `import app.config` works from the
    # extracted parent tree.
    (parent_dir / 'app').mkdir()
    (parent_dir / 'app' / '__init__.py').write_text('')
    (parent_dir / 'app' / 'config.py').write_text(r.stdout)
    probe = 'from app.config import DOWNLOADS_DIR; print(DOWNLOADS_DIR)'
    env = _sandbox_env(tmp_path / 'home2')
    (env and Path(env['HOME'])).mkdir(parents=True, exist_ok=True)
    r2 = subprocess.run(
        [sys.executable, '-c', probe],
        cwd=str(parent_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r2.returncode == 0, r2.stderr
    parent_downloads = Path(r2.stdout.strip())
    expected = Path(env['HOME']) / '.vibe-seller' / 'downloads'
    assert parent_downloads == expected


@pytest.mark.unit
def test_negative_control_diverted_home_breaks_equality(tmp_path: Path):
    """Change the home resolution (as a changed default-home formula
    would) and the equality detector reports a mismatch — the test
    above fails rather than silently passing."""
    home = tmp_path / 'home'
    home.mkdir()
    # sitecustomize runs before the app and diverts Path.home, the
    # exact seam a formula change would alter.
    site = tmp_path / 'sitecustomize.py'
    site.write_text(
        'import pathlib\n'
        'pathlib.Path.home = lambda: pathlib.Path('
        + repr(str(tmp_path / 'changed-home'))
        + ')\n',
        encoding='utf-8',
    )
    values = _run_probe(home, extra_pythonpath=tmp_path)
    expected = str(home / '.vibe-seller' / 'downloads' / SLUG)
    assert values['env_var'] != expected
    assert values['mux_pin'] != expected
