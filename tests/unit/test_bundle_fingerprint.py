"""Tests for the runtime-bundle manifest and its fingerprint.

The fingerprint is the whole pip-vs-installer decision on Windows, so
these tests are the decision's specification: same inputs → same value,
any runtime change → a different one, and nothing the installer lays
down outside the venv may sit outside the inputs.
"""

import json
from pathlib import Path
import re

import pytest

from app import bundle

pytestmark = pytest.mark.unit

_ROOT = bundle.repo_root()

_PINS = {
    'format': 1,
    'python': {'pbs_tag': '20250115', 'version': '3.11.11'},
    'git_for_windows': {'version': '2.47.1'},
    'uv': {'version': '0.5.18'},
    'claude_code': {'version': '2.1.323'},
    'playwright': {'version': '1.65.1'},
}

_PYPROJECT = """
[project]
name = "vibe-seller"
dependencies = [
    "fastapi",
    "playwright==1.65.1",
    "psutil>=5.9.0",
]
"""


def _fake_root(tmp_path: Path, name: str = 'repo') -> Path:
    """A minimal checkout-shaped tree carrying the real manifest shape.

    Every call builds its own tree: mutating one to prove a pin moves
    the fingerprint must not leak into the next assertion. Callers in a
    loop pass a distinct *name* per iteration.
    """
    root = tmp_path / name
    (root / 'app').mkdir(parents=True)
    (root / bundle.PINS_PATH).write_text(
        json.dumps(json.loads(json.dumps(_PINS))), encoding='utf-8'
    )
    (root / bundle.PYPROJECT_PATH).write_text(_PYPROJECT, encoding='utf-8')
    for rel in bundle.INSTALLER_OWNED_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'# {rel}\n', encoding='utf-8')
    return root


def _set_pin(root: Path, head: str, tail: str, value: str) -> None:
    path = root / bundle.PINS_PATH
    pins = json.loads(path.read_text(encoding='utf-8'))
    pins[head][tail] = value
    path.write_text(json.dumps(pins), encoding='utf-8')


class TestManifestAgreesWithPyproject:
    def test_the_shipped_manifest_matches(self):
        """The one check that matters in production: one Playwright."""
        pins = bundle.assert_pins_agree(_ROOT)
        assert pins['playwright']['version'] == (
            bundle.pyproject_playwright_pin(_ROOT)
        )

    def test_a_disagreement_is_refused(self, tmp_path):
        root = _fake_root(tmp_path)
        (root / bundle.PINS_PATH).write_text(
            json.dumps({**_PINS, 'playwright': {'version': '1.66.0'}}),
            encoding='utf-8',
        )
        with pytest.raises(bundle.PinMismatch, match='1.66.0'):
            bundle.assert_pins_agree(root)

    def test_a_floating_playwright_pin_is_refused(self, tmp_path):
        """`playwright` unpinned is what made the bundle float; the
        manifest cannot fingerprint a version nobody chose."""
        root = _fake_root(tmp_path)
        (root / bundle.PYPROJECT_PATH).write_text(
            _PYPROJECT.replace('playwright==1.65.1', 'playwright>=1.60'),
            encoding='utf-8',
        )
        with pytest.raises(bundle.PinMismatch, match='pinned exactly'):
            bundle.pyproject_playwright_pin(root)

    def test_a_missing_playwright_dependency_is_refused(self, tmp_path):
        root = _fake_root(tmp_path)
        (root / bundle.PYPROJECT_PATH).write_text(
            _PYPROJECT.replace('    "playwright==1.65.1",\n', ''),
            encoding='utf-8',
        )
        with pytest.raises(bundle.PinMismatch, match='not a declared'):
            bundle.pyproject_playwright_pin(root)

    def test_a_manifest_without_the_file_is_refused(self, tmp_path):
        root = _fake_root(tmp_path)
        (root / bundle.PINS_PATH).unlink()
        with pytest.raises((OSError, ValueError)):
            bundle.assert_pins_agree(root)


class TestFingerprint:
    def test_the_version_is_not_an_input(self, tmp_path):
        """Two releases, same runtime → same fingerprint. THAT is the
        signal the pip path is built on."""
        root = _fake_root(tmp_path)
        a = bundle.bundle_document('0.3.1', root)
        b = bundle.bundle_document('0.3.2', root)
        assert a['fingerprint'] == b['fingerprint']
        assert a['version'] != b['version']

    def test_the_same_tree_rebuilds_to_the_same_value(self, tmp_path):
        """Determinism across machines, which is what lets the release
        asset and the installed file be compared at all."""
        first = bundle.bundle_document('0.3.1', _fake_root(tmp_path, 'a'))
        second = bundle.bundle_document('0.3.1', _fake_root(tmp_path, 'b'))
        assert first == second

    def test_an_app_only_change_keeps_the_fingerprint(self, tmp_path):
        root = _fake_root(tmp_path)
        before = bundle.bundle_document('0.3.1', root)
        (root / 'app' / 'main.py').write_text('print("new code")\n')
        (root / 'frontend').mkdir()
        (root / 'frontend' / 'src').mkdir()
        (root / 'frontend' / 'src' / 'App.tsx').write_text('// new\n')
        after = bundle.bundle_document('0.3.2', root)
        assert before['fingerprint'] == after['fingerprint']

    def test_a_pin_change_moves_the_fingerprint(self, tmp_path):
        # Every pin in the manifest must reach the value, not just the
        # ones someone happens to remember to bump.
        changes = [
            ('uv', 'version'),
            ('python', 'version'),
            ('python', 'pbs_tag'),
            ('git_for_windows', 'version'),
            ('claude_code', 'version'),
        ]
        for head, tail in changes:
            root = _fake_root(tmp_path, name=f'{head}-{tail}')
            base = bundle.bundle_document('0.3.1', root)['fingerprint']
            _set_pin(root, head, tail, '999999')
            moved = bundle.bundle_document('0.3.1', root)['fingerprint']
            assert moved != base, f'{head}.{tail} is not an input'

    def test_an_installer_file_change_moves_the_fingerprint(self, tmp_path):
        for rel in bundle.INSTALLER_OWNED_FILES:
            root = _fake_root(tmp_path, name=rel.replace('/', '-'))
            base = bundle.bundle_document('0.3.1', root)['fingerprint']
            (root / rel).write_text('# changed\n', encoding='utf-8')
            moved = bundle.bundle_document('0.3.1', root)['fingerprint']
            assert moved != base, f'{rel} is not an input'

    def test_a_missing_input_file_fails_closed(self, tmp_path):
        root = _fake_root(tmp_path)
        (root / 'installer' / 'windows' / 'tray.py').unlink()
        with pytest.raises(FileNotFoundError, match='tray.py'):
            bundle.bundle_document('0.3.1', root)

    def test_json_key_order_does_not_matter(self, tmp_path):
        """The pins are authored by hand and parsed by two languages
        (PowerShell and Python); key order must not move the value."""
        doc = bundle.bundle_document('0.3.1', _fake_root(tmp_path))
        reversed_doc = bundle.fingerprint_document(
            dict(reversed(list(doc['pins'].items()))),
            dict(reversed(list(doc['files'].items()))),
        )
        assert bundle.fingerprint_of(reversed_doc) == doc['fingerprint']


class TestLineEndings:
    """The fingerprint must say what the inputs MEAN, not how the
    machine that checked them out spelled their newlines.

    `actions/checkout` on the Windows installer build sets
    `core.autocrlf=true`; a Linux job (and this repo, and a developer's
    WSL) has LF. Raw-byte hashing gives those two checkouts different
    fingerprints for one commit, and a client that cannot ever see an
    equal pair silently falls back to the ~700 MB installer forever —
    the failure mode is the feature not working, not an error.
    """

    TEXT_INPUTS = [
        rel for rel in bundle.INSTALLER_OWNED_FILES if rel.endswith('.py')
    ] + ['installer/windows/vibe-seller.iss']

    def test_a_text_input_digests_the_same_either_way(self, tmp_path):
        for rel in self.TEXT_INPUTS:
            here = tmp_path / 'here' / rel
            there = tmp_path / 'there' / rel
            for path in (here, there):
                path.parent.mkdir(parents=True, exist_ok=True)
            text = '# a line\n# another\n'
            here.write_bytes(text.encode())
            there.write_bytes(text.replace('\n', '\r\n').encode())
            assert bundle.file_digest(here) == bundle.file_digest(there), rel

    def test_the_fingerprint_survives_a_whole_crlf_checkout(self, tmp_path):
        """The real scenario: the installer build sees every text input
        as CRLF, the job publishing the release asset sees LF."""
        lf = _fake_root(tmp_path, 'lf')
        crlf = _fake_root(tmp_path, 'crlf')
        for rel in bundle.INSTALLER_OWNED_FILES:
            path = crlf / rel
            if bundle.hashed_as_text(path):
                path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        a = bundle.bundle_document('0.3.2', lf)
        b = bundle.bundle_document('0.3.2', crlf)
        assert a['fingerprint'] == b['fingerprint']

    def test_a_content_change_under_crlf_still_moves_it(self, tmp_path):
        """Normalising must not blind the hash: drop a line from the
        CRLF tree and the fingerprints part company."""
        lf = _fake_root(tmp_path, 'lf')
        crlf = _fake_root(tmp_path, 'crlf')
        for rel in bundle.INSTALLER_OWNED_FILES:
            path = crlf / rel
            if bundle.hashed_as_text(path):
                path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        before = bundle.bundle_document('0.3.2', crlf)['fingerprint']
        assert bundle.bundle_document('0.3.2', lf)['fingerprint'] == before
        (crlf / 'installer' / 'windows' / 'tray.py').write_bytes(
            b'# changed\r\n'
        )
        assert bundle.bundle_document('0.3.2', crlf)['fingerprint'] != before
        assert bundle.bundle_document('0.3.2', lf)['fingerprint'] == before

    def test_binary_inputs_are_hashed_as_bytes(self, tmp_path):
        """An .ico has no line endings; normalising it would let a real
        1-byte change hide inside a CR-LF pair."""
        rel = 'installer/windows/vibe-seller.ico'
        path = tmp_path / rel
        path.parent.mkdir(parents=True)
        path.write_bytes(b'\x00\x01\r\n\x02')
        crlf = bundle.file_digest(path)
        assert bundle.hashed_as_text(path) is False
        path.write_bytes(b'\x00\x01\n\x02')
        assert bundle.file_digest(path) != crlf

    def test_every_input_has_a_declared_hash_mode(self):
        """Adding an input with a suffix no one thought about must be a
        decision, not a silent byte-hash of a text file.

        A `.bat`/`.cmd`/`.isl` added to the bundle would arrive here as
        CRLF on the build machine and LF elsewhere — exactly the bug the
        class above exists to prevent.
        """
        known_binary = {'.ico'}
        unknown = {
            Path(rel).suffix.lower()
            for rel in bundle.INSTALLER_OWNED_FILES
            if Path(rel).suffix.lower() not in bundle._HASHED_AS_TEXT
            and Path(rel).suffix.lower() not in known_binary
        }
        assert not unknown, (
            f'fingerprint inputs with suffixes {unknown} are hashed as '
            f'raw bytes; add them to app/bundle.py:_HASHED_AS_TEXT if '
            f'they are text'
        )


class TestIssFilesAreAllAccountedFor:
    """Under-inclusion is the dangerous direction: a runtime file the
    installer lays down that no fingerprint input covers would let a
    pip update ship stale content while claiming equality.

    So the .iss's own ``[Files]`` list is the specification, and every
    staged source it installs must be owned by a pin, by a hashed file,
    or by an explicit reason for being outside the bundle.
    """

    #: What each thing build.ps1 stages into the installer is.
    OWNERS = {
        'python': 'pin:python',
        'git': 'pin:git_for_windows',
        'claude': 'pin:claude_code',
        'uv.exe': 'pin:uv',
        'ms-playwright': 'pin:playwright',
        'tray.py': 'file',
        'dialogs.py': 'file',
        'autostart.py': 'file',
        'vibe-seller.ico': 'file',
        # The venv's own content: an update replaces it with uv, so it
        # is deliberately NOT part of the bundle's identity.
        'wheels': 'venv',
        # Written from the entries above; hashing it would be circular.
        'bundle.json': 'derived',
    }

    def _staged_names(self) -> set[str]:
        """The staging-dir entries the [Files] section installs, read
        from the section itself (the string "[Files]" also appears in
        prose comments, so the section is found by its header LINE)."""
        lines = (
            (_ROOT / 'installer' / 'windows' / 'vibe-seller.iss')
            .read_text(encoding='utf-8')
            .splitlines()
        )
        start = lines.index('[Files]')
        section = []
        for line in lines[start + 1 :]:
            if line.startswith('['):
                break
            section.append(line)
        return set(
            re.findall(
                r'Source:\s*"\{#StagingDir\}\\([^"\\]+)', '\n'.join(section)
            )
        )

    def test_the_files_section_is_actually_parsed(self):
        """Guards every test above against a vacuous pass on a broken
        extractor."""
        assert 'tray.py' in self._staged_names()
        assert len(self._staged_names()) > 8

    def test_every_staged_source_has_an_owner(self):
        unowned = self._staged_names() - self.OWNERS.keys()
        assert not unowned, (
            'the installer stages these outside the venv and nothing in '
            'app/bundle.py accounts for them: ' + ', '.join(sorted(unowned))
        )

    def test_every_owner_the_iss_no_longer_uses_is_removed(self):
        unused = self.OWNERS.keys() - self._staged_names()
        assert not unused, f'stale bundle owners: {", ".join(sorted(unused))}'

    def test_hashed_owners_are_the_fingerprint_inputs(self):
        hashed = {k for k, v in self.OWNERS.items() if v == 'file'}
        assert hashed == {Path(rel).name for rel in bundle.BUNDLED_FILES}

    def test_the_installer_script_itself_is_an_input(self):
        """The .iss is compiled into Setup.exe rather than installed,
        so it never appears in [Files] — but it decides where every
        entry above lands, so it must move the fingerprint."""
        assert bundle.INSTALLER_SCRIPTS == (
            'installer/windows/vibe-seller.iss',
        )
        assert set(bundle.INSTALLER_OWNED_FILES) == set(
            bundle.INSTALLER_SCRIPTS
        ) | set(bundle.BUNDLED_FILES)

    def test_every_pin_names_something_the_installer_lays_down(self):
        pinned = {
            v.split(':', 1)[1]
            for v in self.OWNERS.values()
            if v.startswith('pin:')
        }
        manifest = set(bundle.load_pins(_ROOT)) - {'format'}
        assert pinned == manifest, (
            'a pin nothing installs, or an install nothing pins: '
            f'{pinned ^ manifest}'
        )


class TestBundleComparison:
    def _doc(self, fingerprint='a' * 64, **kw):
        return {'fingerprint': fingerprint, **kw}

    def test_equal_fingerprints_take_the_pip_path(self):
        assert bundle.pip_path_applies(self._doc(), self._doc()) is True

    def test_different_fingerprints_take_the_installer(self):
        assert (
            bundle.pip_path_applies(self._doc('b' * 64), self._doc('a' * 64))
            is False
        )

    def test_a_release_without_the_asset_takes_the_installer(self):
        assert bundle.pip_path_applies(None, self._doc()) is False

    def test_an_install_without_bundle_json_takes_the_installer(self):
        """0.3.1 predates bundle.json — it can never be pip-updated."""
        assert bundle.pip_path_applies(self._doc(), None) is False

    def test_a_malformed_bundle_reads_as_missing(self, tmp_path):
        bad = tmp_path / 'bundle.json'
        bad.write_text('{not json', encoding='utf-8')
        assert bundle.read_bundle(bad) is None
        bad.write_text('{"version": "0.3.1"}', encoding='utf-8')
        assert bundle.read_bundle(bad) is None
        assert bundle.read_bundle(tmp_path / 'nope.json') is None

    def test_a_written_bundle_reads_back_equal(self, tmp_path):
        doc = bundle.bundle_document('0.3.2', _fake_root(tmp_path))
        path = tmp_path / 'out' / 'bundle.json'
        bundle.write_bundle(doc, path)
        assert bundle.read_bundle(path) == doc


# ── the floating pins: latest in the repo, exact in every build ──────


def _resolver():
    """``scripts/resolve_bundle_pins.py`` as a module (it is a script,
    not a package member)."""
    import importlib.util  # noqa: PLC0415

    path = _ROOT / 'scripts' / 'resolve_bundle_pins.py'
    spec = importlib.util.spec_from_file_location('resolve_pins', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _floating_root(tmp_path: Path) -> Path:
    root = _fake_root(tmp_path)
    for name in bundle.FLOATING_PINS:
        _set_pin(root, name, 'version', bundle.LATEST)
    (root / bundle.PYPROJECT_PATH).write_text(
        _PYPROJECT.replace('playwright==1.65.1', 'playwright'),
        encoding='utf-8',
    )
    return root


class TestFloatingPins:
    def test_the_repo_pins_neither_claude_nor_playwright(self):
        """The decision this pins: the source tree follows upstream, and
        only a build chooses versions."""
        pins = bundle.load_pins(_ROOT)
        for name in bundle.FLOATING_PINS:
            assert pins[name]['version'] == bundle.LATEST, name
        assert bundle.pyproject_playwright_pin(_ROOT) == bundle.LATEST

    def test_a_bare_playwright_reads_as_latest_and_agrees(self, tmp_path):
        root = _floating_root(tmp_path)
        assert bundle.pyproject_playwright_pin(root) == bundle.LATEST
        pins = bundle.assert_pins_agree(root)
        assert bundle.unresolved_pins(pins) == list(bundle.FLOATING_PINS)

    def test_stamping_makes_the_wheel_and_the_manifest_agree(self, tmp_path):
        root = _floating_root(tmp_path)
        pins = bundle.stamp_pins(
            root, {'claude_code': '2.2.0', 'playwright': '1.70.0'}
        )
        assert bundle.unresolved_pins(pins) == []
        assert pins['claude_code']['version'] == '2.2.0'
        assert bundle.pyproject_playwright_pin(root) == '1.70.0'
        assert bundle.pinned_claude_version(root) == '2.2.0'
        # A second stamp (a build told to use another version) replaces
        # the first rather than appending a second requirement.
        bundle.stamp_pins(root, {'playwright': '1.71.0'})
        text = (root / bundle.PYPROJECT_PATH).read_text(encoding='utf-8')
        assert text.count('"playwright') == 1
        assert bundle.assert_pins_agree(root)['playwright']['version'] == (
            '1.71.0'
        )

    def test_a_malformed_version_never_reaches_a_file(self, tmp_path):
        root = _floating_root(tmp_path)
        before = (root / bundle.PINS_PATH).read_text(encoding='utf-8')
        with pytest.raises(ValueError, match='not a version'):
            bundle.stamp_pins(root, {'claude_code': '2.0; rm -rf /'})
        assert (root / bundle.PINS_PATH).read_text(encoding='utf-8') == before

    def test_the_resolved_versions_decide_pip_or_installer(self, tmp_path):
        """Same resolution → same fingerprint (a pip update); a newer
        claude or Playwright → a different one (the installer)."""
        a = _floating_root(tmp_path / 'a')
        b = _floating_root(tmp_path / 'b')
        c = _floating_root(tmp_path / 'c')
        same = {'claude_code': '2.2.0', 'playwright': '1.70.0'}
        bundle.stamp_pins(a, same)
        bundle.stamp_pins(b, same)
        bundle.stamp_pins(c, {**same, 'claude_code': '2.2.1'})
        fa = bundle.bundle_document('1', a)['fingerprint']
        assert fa == bundle.bundle_document('2', b)['fingerprint']
        assert fa != bundle.bundle_document('2', c)['fingerprint']


class TestResolverChoice:
    def _fetch(self, name):
        return {'claude_code': '9.9.9', 'playwright': '8.8.8'}[name]

    def test_newest_when_nothing_is_said(self):
        got = _resolver().choose('claude_code', None, {}, 'latest', self._fetch)
        assert got == ('9.9.9', 'newest release')

    def test_an_argument_beats_the_environment(self):
        env = {'BUNDLE_CLAUDE_VERSION': '2.0.0'}
        got = _resolver().choose(
            'claude_code', '2.1.0', env, 'latest', self._fetch
        )
        assert got[0] == '2.1.0'

    def test_the_environment_beats_a_resolved_tree(self):
        env = {'BUNDLE_PLAYWRIGHT_VERSION': '1.60.0'}
        got = _resolver().choose('playwright', None, env, '1.65.1', self._fetch)
        assert got == ('1.60.0', 'BUNDLE_PLAYWRIGHT_VERSION')

    def test_a_resolved_tree_keeps_its_answer(self):
        """build.ps1 resolves again after a workflow already did: it
        must not drift to a release that appeared in between."""
        got = _resolver().choose('playwright', None, {}, '1.65.1', self._fetch)
        assert got == ('1.65.1', 'already resolved')

    def test_latest_or_empty_is_no_override(self):
        env = {'BUNDLE_CLAUDE_VERSION': ''}
        got = _resolver().choose(
            'claude_code', 'latest', env, 'latest', self._fetch
        )
        assert got[0] == '9.9.9'


class TestReleaseBundleMustBeResolved:
    def test_the_build_refuses_a_bundle_that_says_latest(
        self, tmp_path, monkeypatch, capsys
    ):
        import importlib.util  # noqa: PLC0415

        root = _floating_root(tmp_path)
        monkeypatch.setattr(bundle, 'repo_root', lambda: root)
        path = _ROOT / 'scripts' / 'bundle_fingerprint.py'
        spec = importlib.util.spec_from_file_location('bfp', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.main(['--print', '--require-resolved']) == 2
        assert 'still say "latest"' in capsys.readouterr().err
        # The cross-OS agreement check fingerprints a checkout as it is.
        assert mod.main(['--print']) == 0
