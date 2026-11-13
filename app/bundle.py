"""The Windows runtime bundle: what it pins, and how to name it.

The Windows installer ships a whole self-contained runtime — a
relocatable CPython, Git for Windows, uv, the Claude Code CLI and
Playwright's Chromium — next to the venv the app code lives in. Only
the app code travels in the wheel, so an update that changes just the
wheel can be installed with `uv pip install` (a few MB) instead of
re-running the ~700 MB installer.

That choice needs one answer: **does the new release change anything
outside the venv?** A version number cannot answer it — 0.3.1 → 0.3.2
says nothing about the bundle. This module names the thing that does:
the bundle *fingerprint*, a hash of every input the installer lays down
beside the venv (``app/bundle_pins.json`` plus the installer-owned files
themselves). Two releases with the same fingerprint ship the same
runtime, so the newer one is a pip update.

The fingerprint is computed at BUILD time (``scripts/bundle_fingerprint.py``
→ ``installer/windows/build.ps1``), embedded in the installer as
``{app}\\bundle.json`` and published beside it as the release asset
``bundle.json``. At run time nothing here re-hashes the tree: a source
checkout has no bundle at all, and an installed one reads what its
installer wrote. So ``installed_fingerprint()`` is the local half of the
comparison and the release asset is the remote half — equal means pip,
different or missing on either side means the installer.

Keeping the pins honest is a second job of this module. ``playwright``
is a Python dependency, so its pin lives in ``pyproject.toml`` — but it
also fixes which Chromium revision the installer bundles, so it belongs
in the manifest too. ``pyproject_playwright_pin()`` reads the real one
and :func:`assert_pins_agree` refuses a manifest that disagrees, which
is what stops the fingerprint from describing a bundle nobody built.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any

#: Repo-relative. The single source of truth for the bundled versions;
#: ``installer/windows/build.ps1`` reads its parameters from it.
PINS_PATH = 'app/bundle_pins.json'
PYPROJECT_PATH = 'pyproject.toml'

#: The installer's own definition. Not laid down on disk — it is
#: compiled into ``VibeSeller-Setup.exe`` — but it decides where every
#: bundled file goes, how the venv is built and what the tray's PATH
#: looks like, so a change to it is a change to the install.
INSTALLER_SCRIPTS = ('installer/windows/vibe-seller.iss',)

#: Everything the installer puts on disk outside the venv, apart from
#: the pinned runtimes named in the manifest. A change to any of these
#: moves the fingerprint, because the pip path never replaces them.
#:
#: Deliberately absent: ``build.ps1`` (build-time only — it fetches the
#: files listed here, it is not one of them) and ``make_icon.py``
#: (generates ``vibe-seller.ico``, which IS installed and hashed). The
#: [Files] entries the installer really lays down are pinned to this
#: list by ``tests/unit/test_bundle_fingerprint.py``.
BUNDLED_FILES = (
    'installer/windows/tray.py',
    'installer/windows/dialogs.py',
    'installer/windows/autostart.py',
    'installer/windows/vibe-seller.ico',
)

#: The fingerprint's file inputs, in one tuple so nothing can forget a
#: half of them.
INSTALLER_OWNED_FILES = INSTALLER_SCRIPTS + BUNDLED_FILES

#: Bumping this means the fingerprint input set changed shape, so every
#: existing install must take the installer path at least once.
FORMAT_VERSION = 1

#: Name of the release asset / installed file that carries a bundle
#: document.
BUNDLE_ASSET_NAME = 'bundle.json'


class PinMismatch(RuntimeError):
    """The manifest and ``pyproject.toml`` name different versions of a
    dependency that is also a bundle pin."""


def repo_root() -> Path:
    """The source tree this module was imported from (a checkout)."""
    return Path(__file__).resolve().parents[1]


# ── the manifest ─────────────────────────────────────────────────────


def load_pins(root: Path | None = None) -> dict[str, Any]:
    """The bundle manifest, as a plain dict.

    Reads the file inside the package, so an installed app and a source
    checkout both answer from the tree they came from. Raises if it is
    missing or unreadable: with no manifest there is no bundle to talk
    about, and a silent empty dict would fingerprint every release
    alike."""
    base = root if root is not None else repo_root()
    path = base / PINS_PATH
    doc = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(doc, dict) or 'playwright' not in doc:
        raise ValueError(f'{path} is not a bundle manifest')
    return doc


def pinned_claude_version(root: Path | None = None) -> str:
    """The Claude Code CLI version every platform should be running.

    ``vibe-seller upgrade`` installs exactly this instead of
    ``@latest``, so the CLI the installer bundles and the one the Unix
    upgrade pulls are the one that was tested with the release."""
    pins = load_pins(root)
    return str(pins['claude_code']['version'])


def pinned_playwright_version(root: Path | None = None) -> str:
    """The manifest's Playwright pin (which fixes the bundled Chromium)."""
    pins = load_pins(root)
    return str(pins['playwright']['version'])


#: ``name[extras] ==specifier`` — the PEP 508 shapes a dependency line
#: can take, with the extras optional. The version SPEC is what matters
#: here, and only an exact ``==`` is acceptable (see below).
_REQ_SPLIT = re.compile(
    r'^(?P<name>[A-Za-z0-9._-]+)\s*(\[[^\]]*\])?\s*(?P<spec>.*)$'
)


def pyproject_playwright_pin(root: Path | None = None) -> str:
    """The Playwright pin ``pyproject.toml`` installs (the real one).

    Parsed with ``tomllib`` rather than imported from the installed
    metadata: this runs inside the installer build, where vibe-seller is
    not installed and only the source tree is.
    """
    base = root if root is not None else repo_root()
    data = tomllib.loads((base / PYPROJECT_PATH).read_text(encoding='utf-8'))
    for raw in data.get('project', {}).get('dependencies', []):
        parts = _REQ_SPLIT.match(str(raw).strip())
        if not parts or parts.group('name').lower() != 'playwright':
            continue
        spec = parts.group('spec').strip()
        exact = re.fullmatch(r'==\s*([^,;\s]+)', spec)
        if not exact:
            raise PinMismatch(
                f'playwright must be pinned exactly in pyproject.toml '
                f'(playwright==<version>); found {raw!r}. Its version '
                'fixes the Chromium revision the Windows installer '
                'bundles, so an open range makes the bundle float.'
            )
        return exact.group(1)
    raise PinMismatch(
        'playwright is not a declared dependency of pyproject.toml, but '
        'the bundle manifest pins it — the installer would bundle a '
        'Chromium no installed Playwright asks for.'
    )


def assert_pins_agree(root: Path | None = None) -> dict[str, Any]:
    """Load the manifest and prove it matches ``pyproject.toml``.

    Raises :class:`PinMismatch` on a disagreement. Called by
    ``scripts/bundle_fingerprint.py`` (so a release cannot be built
    from two answers) and by a unit test (so it cannot be merged).
    """
    base = root if root is not None else repo_root()
    pins = load_pins(base)
    manifest = str(pins['playwright']['version'])
    real = pyproject_playwright_pin(base)
    if manifest != real:
        raise PinMismatch(
            f'the bundle manifest pins playwright {manifest} but '
            f'pyproject.toml installs {real} — the fingerprint would '
            'describe a Chromium the wheel never runs'
        )
    return pins


# ── the fingerprint ──────────────────────────────────────────────────


#: Suffixes hashed as TEXT: line endings are normalised to ``\n``
#: before hashing, so a file's digest says what it SAYS, not how the
#: checkout happened to spell its newlines.
#:
#: This is load-bearing, not tidiness. `.gitattributes` pins LF only for
#: `*.sh`; everything else follows the machine's `core.autocrlf`, which
#: `actions/checkout` sets to `true` on the Windows runner and leaves at
#: `input` on Linux. So `tray.py` is CRLF bytes in the installer build
#: and LF bytes everywhere else. Without normalisation the same commit
#: would carry two fingerprints depending on which job computed it —
#: and since the Windows build and the release asset are produced on
#: different machines (and a test on WSL, and a developer's checkout on
#: a third), every client would conclude "the bundle changed" and take
#: the ~700 MB installer for the rest of its life. The feature would
#: fail silently: nothing errors, the pip path just never happens.
_HASHED_AS_TEXT = frozenset({'.iss', '.py', '.ps1', '.md', '.txt', '.toml'})


def hashed_as_text(path: Path) -> bool:
    """Whether *path* is normalised before hashing (text) or not (binary)."""
    return path.suffix.lower() in _HASHED_AS_TEXT


def file_digest(path: Path) -> str:
    """sha256 of one fingerprint input, in its declared mode."""
    raw = path.read_bytes()
    if hashed_as_text(path):
        # `\r\n` → `\n`, and a lone trailing CR before EOF is left alone:
        # only the pair is a line ending. A file of CRLF bytes and the
        # same file of LF bytes therefore hash identically, while any
        # real content change still moves the value.
        raw = raw.replace(b'\r\n', b'\n')
    digest = hashlib.sha256()
    digest.update(raw)
    return digest.hexdigest()


def file_digests(root: Path | None = None) -> dict[str, str]:
    """sha256 of every installer-owned file, keyed repo-relative."""
    base = root if root is not None else repo_root()
    out: dict[str, str] = {}
    for rel in INSTALLER_OWNED_FILES:
        path = base / rel
        if not path.is_file():
            raise FileNotFoundError(
                f'{rel} is a fingerprint input but is not in {base} — '
                'a bundle built without it would be indistinguishable '
                'from one that has it'
            )
        out[rel] = file_digest(path)
    return out


def fingerprint_document(pins: dict[str, Any], files: dict[str, str]) -> dict:
    """The hashed document. Key order is fixed by ``sort_keys``, so the
    same inputs always hash to the same value however the JSON arrived."""
    return {
        'format': FORMAT_VERSION,
        'pins': {k: v for k, v in pins.items() if k != 'format'},
        'files': files,
    }


def fingerprint_of(doc: dict) -> str:
    canonical = json.dumps(doc, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def bundle_document(version: str, root: Path | None = None) -> dict[str, Any]:
    """The ``bundle.json`` document: the pins, the file digests, the
    fingerprint over both, and the app version it was built for.

    *version* is descriptive only — it is never hashed, so two releases
    with identical runtime inputs carry identical fingerprints, which is
    exactly the pip-path signal.
    """
    base = root if root is not None else repo_root()
    pins = assert_pins_agree(base)
    files = file_digests(base)
    doc = fingerprint_document(pins, files)
    return {
        'version': version,
        'pins': pins,
        'files': files,
        'fingerprint': fingerprint_of(doc),
    }


def canonical_json(doc: dict) -> str:
    """The one way a bundle document is written, so the release asset
    and the installed file cannot differ by formatting."""
    return json.dumps(doc, indent=2, sort_keys=True) + '\n'


def write_bundle(doc: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(doc), encoding='utf-8')
    return path


# ── the installed side ───────────────────────────────────────────────


def app_root() -> Path | None:
    """The installer's ``{app}`` dir, or None when not bundle-installed.

    The bundle lays ``python\\``, ``git\\``, ``claude\\`` and
    ``bundle.json`` one level above the venv the server runs from — the
    same relation ``app.auto_update.supported()`` uses to spot
    ``unins000.exe``.
    """
    if sys.platform != 'win32':
        return None
    return Path(sys.prefix).parent


def bundle_file(root: Path | None = None) -> Path | None:
    app = root if root is not None else app_root()
    return None if app is None else app / BUNDLE_ASSET_NAME


def read_bundle(path: Path | None) -> dict | None:
    """Parse a bundle document, or None when it is absent or malformed.

    A missing bundle means "this install predates bundle.json" — the
    conservative answer is the installer path, never a pip update that
    assumes a runtime nobody recorded."""
    if path is None:
        return None
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get('fingerprint'), str):
        return None
    return doc


def installed_fingerprint(root: Path | None = None) -> str | None:
    """The fingerprint of the bundle on this machine, or None."""
    doc = read_bundle(bundle_file(root))
    return None if doc is None else doc['fingerprint']


def pip_path_applies(
    release_bundle: dict | None, installed: dict | None
) -> bool:
    """Whether a release may be installed over the current venv.

    True only when BOTH sides carry a bundle document and their
    fingerprints are equal: the new release changes nothing outside the
    venv, so the wheels are the whole update. Anything else — a release
    with no asset, an install predating it, a changed pin — is the
    installer's job."""
    if not release_bundle or not installed:
        return False
    mine = installed.get('fingerprint')
    theirs = release_bundle.get('fingerprint')
    return bool(mine) and mine == theirs
