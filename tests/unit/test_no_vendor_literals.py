"""No anti-detect vendor key literal in shared code.

The target invariant (ARCHITECTURE.md): a new vendor = one vendor module
(+ translations + one docs page), ZERO edits to shared code. This test
is what keeps that true as the codebase drifts: it fails when a shared
module contains a vendor key literal, so a per-vendor branch cannot
land quietly.

What counts as shared: everything under ``app/`` and ``frontend/src/``
EXCEPT

* ``app/builtin_plugin.py`` — the declaration site where core's vendors
  are registered (the one place allowed to name them, like a config);
* vendor modules — files whose NAME carries the vendor key
  (``app/routers/yuehulian.py``, ``app/browser/adapters/yuehulian*.py``,
  the ``ziniao*`` family), which is exactly the "vendor knowledge lives
  in the vendor module" rule;
* frontend i18n locale files and tests — translations are part of a
  vendor's declared contribution, and tests may pin any vendor.

Grandfathered / compat exemptions (each with a reason):

* ``ziniao`` — R6: ziniao stays on its own path until its migration.
* ``yuehulian`` in the documented compat files (``_COMPAT_FILES``): the
  ``YuehulianSection`` shim an existing frontend test renders
  (docsHelpLinks.test.tsx) and the restore rule for the /api/yuehulian
  compat alias — both go away with that route.
* Any vendor key in the in-flight contract PR's files
  (``_IN_FLIGHT_CONTRACT_FILES``) — the remaining file is not this
  refactor's to edit. Shrink the set as #326 cleans its files.

A vendor registered through the plugin context is checked automatically
(the registry is the source of truth), plus ``wenmai`` — in flight on
another branch, never allowed in shared code.
"""

from pathlib import Path

import pytest

from app.browser.vendors import (
    KIND_ANTIDETECT,
    registered_vendor_specs,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]

# Vendor keys that must never appear in shared code beyond the
# exemptions above. The registered anti-detect keys are read from the
# registry at collection time; 'wenmai' is pinned because its
# registration lands in a different PR (its files do not exist here).
_EXTRA_KEYS = ('wenmai',)

# Shared files with a documented yuehulian COMPAT surface (see the
# module docstring): each entry exists only while the /api/yuehulian
# compat alias (and the test that renders the old section) does.
_COMPAT_FILES = {
    # The YuehulianSection compat shim docsHelpLinks.test.tsx renders.
    'frontend/src/components/StorePickers.tsx',
    # The restore rule for the /api/yuehulian compat alias route.
    'app/rest_secret_guard.py',
}

# Files owned by the in-flight contract PR (#326) that still name
# vendors in docstrings/comments. Its vendor-neutral update already
# cleaned inject/client.py, engine.py and win_inspector.py (removed
# from this set — the test now guards them like any other file);
# yuehulian_win.py remains until its wenmai mentions go.
_IN_FLIGHT_CONTRACT_FILES = {
    'app/browser/adapters/yuehulian_win.py',
}

_EXCLUDED_PATHS = ('app/builtin_plugin.py',)


def _vendor_keys() -> list[str]:
    keys = {
        key
        for key, spec in registered_vendor_specs().items()
        if spec.kind == KIND_ANTIDETECT
    }
    keys.update(_EXTRA_KEYS)
    keys.discard('ziniao')  # grandfathered (R6)
    return sorted(keys)


def _is_vendor_module(path: Path, key: str) -> bool:
    """A file whose name carries the vendor key is the vendor's module."""
    return key in path.name.lower()


def shared_files() -> list[Path]:
    files: list[Path] = []
    files.extend(ROOT.joinpath('app').rglob('*.py'))
    for pattern in ('*.ts', '*.tsx'):
        files.extend(ROOT.joinpath('frontend', 'src').rglob(pattern))
    out = []
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        if rel in _EXCLUDED_PATHS:
            continue
        if rel.startswith('frontend/src/i18n/'):
            continue
        if '__tests__' in f.parts:
            continue
        out.append(f)
    return out


def find_violations(keys: list[str], files: list[Path]) -> list[str]:
    """(file, key) pairs where a shared file names a vendor key."""

    def _rel(f: Path) -> str:
        try:
            return f.relative_to(ROOT).as_posix()
        except ValueError:
            return f.name

    bad = []
    for f in files:
        rel = _rel(f)
        text = f.read_text(encoding='utf-8', errors='replace')
        for key in keys:
            if key not in text:
                continue
            if _is_vendor_module(f, key):
                continue  # the vendor's own module
            if key == 'yuehulian' and rel in _COMPAT_FILES:
                continue  # documented compat shim
            if rel in _IN_FLIGHT_CONTRACT_FILES:
                continue  # owned by #326 until its vendor-neutral update
            bad.append(f'{rel}: {key}')
    return bad


def test_no_vendor_key_literal_in_shared_code():
    keys = _vendor_keys()
    assert keys, 'the registry must have anti-detect vendors to check'
    bad = find_violations(keys, shared_files())
    assert not bad, (
        'Vendor key literals in shared code — move them into the '
        'vendor module (and its spec):\n  ' + '\n  '.join(bad)
    )


def test_the_checker_catches_a_planted_literal(tmp_path):
    """Negative control: the check itself fails on a violation."""
    planted = tmp_path / 'some_shared_module.py'
    planted.write_text("BACKENDS = {'yuehulian'}\n", encoding='utf-8')
    assert find_violations(['yuehulian'], [planted]) == [
        'some_shared_module.py: yuehulian'
    ]
    # And passes on a clean file.
    clean = tmp_path / 'clean_module.py'
    clean.write_text("kind = 'antidetect'\n", encoding='utf-8')
    assert find_violations(['yuehulian'], [clean]) == []
