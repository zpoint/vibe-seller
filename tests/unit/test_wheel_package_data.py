"""Runtime data files must be named in ``package-data``, not inferred.

`app/prompts` and `app/skills_v2` each carry an `__init__.py`, so they
are packages in their own right rather than data of `app`. Their
non-`.py` files were therefore reaching the wheel only through
setuptools-scm's git file finder (``include-package-data`` is on by
default for pyproject builds).

That finder needs a usable `.git` at build time. Where it has none — an
exported tree, or a build that copies the source away from the repo — it
returns nothing and fails **silently**: the wheel builds clean, installs
clean, and the server dies on first import with
``FileNotFoundError: app/prompts/design_system.md``, because
``app/prompts/__init__.py`` reads the templates at import time.

Rather than build a wheel here (slow, and needs the network for build
deps), this pins the invariant directly: every non-`.py` file the
package ships must sit under a directory named explicitly in
``[tool.setuptools.package-data]``.
"""

from pathlib import Path
import tomllib

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_APP = _ROOT / 'app'

# Not shipped, or not data: caches, compiled output, and the editable
# install's own metadata.
_IGNORED_DIRS = {'__pycache__', '.pytest_cache', '.ruff_cache'}
_IGNORED_SUFFIXES = {'.py', '.pyc', '.pyo', '.pyd', '.so'}
# Per-module developer docs. Every code package under app/ carries one;
# they document the source and are never read at runtime, so whether a
# wheel ships them is immaterial.
_IGNORED_NAMES = {'README.md'}


def _package_data_globs() -> list[str]:
    with open(_ROOT / 'pyproject.toml', 'rb') as fh:
        cfg = tomllib.load(fh)
    return cfg['tool']['setuptools']['package-data']['app']


def _covered_top_dirs() -> set[str]:
    """Top-level dirs under ``app/`` named by a package-data glob."""
    return {g.split('/', 1)[0] for g in _package_data_globs()}


def _shipped_data_files():
    """Every non-``.py`` file under ``app/`` that a wheel would carry."""
    for path in _APP.rglob('*'):
        if not path.is_file():
            continue
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue
        if path.suffix in _IGNORED_SUFFIXES:
            continue
        if path.name in _IGNORED_NAMES:
            continue
        yield path.relative_to(_APP)


def test_every_shipped_data_dir_is_named_in_package_data():
    """No runtime data file may depend on the git file finder."""
    covered = _covered_top_dirs()
    uncovered = sorted({
        rel.parts[0] if len(rel.parts) > 1 else str(rel)
        for rel in _shipped_data_files()
        if not (len(rel.parts) > 1 and rel.parts[0] in covered)
    })
    assert not uncovered, (
        'These ship non-.py files that no package-data glob names, so '
        'they reach the wheel only via setuptools-scm reading git — '
        'which silently ships nothing when .git is absent at build '
        'time:\n  ' + '\n  '.join(uncovered)
    )


@pytest.mark.parametrize('required', ['prompts', 'skills_v2'])
def test_import_time_data_dirs_are_covered(required):
    """The two whose absence stops the server from starting at all."""
    assert required in _covered_top_dirs(), (
        f'app/{required} is read at import time; without an explicit '
        'package-data glob a wheel built without .git omits it and the '
        'server cannot start.'
    )


def test_prompt_templates_are_present_to_be_shipped():
    """Guards the parametrised test above against a vacuous pass."""
    assert (_APP / 'prompts' / 'design_system.md').is_file()
