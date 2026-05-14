"""Unit tests for the gws (Google Workspace CLI) integration.

Strategy: shim the `gws` binary with tests/fixtures/fake_gws.sh on
PATH so tests exercise the real install pipeline (subprocess spawn,
subset filter, cross-ref rewrite, umbrella generation, atomic swap)
without needing network or a real Google account.
"""

import hashlib
from pathlib import Path
import re

import pytest

from app.workspace import gws_integration

FIXTURES = Path(__file__).parent.parent / 'fixtures'
FAKE_GWS = FIXTURES / 'fake_gws.sh'


@pytest.fixture(autouse=True)
def _fixture_exists():
    """Catch accidental fixture removal early."""
    assert FAKE_GWS.is_file(), (
        f'fake_gws.sh missing at {FAKE_GWS} — '
        'did the tests/fixtures dir get cleaned?'
    )
    # Make sure it's executable (git may drop the bit on some clones).
    mode = FAKE_GWS.stat().st_mode
    assert mode & 0o111, (
        f'fake_gws.sh not executable (mode={oct(mode)}). '
        'Run: chmod +x tests/fixtures/fake_gws.sh'
    )


@pytest.fixture
def patched_workspace(monkeypatch, tmp_path):
    """Point VIBE_SELLER_DIR at a tmp dir so install writes here.

    Patches the symbol actually used inside gws_integration (which
    imports VIBE_SELLER_DIR at module load).
    """
    monkeypatch.setattr(gws_integration, 'VIBE_SELLER_DIR', tmp_path)
    (tmp_path / '.claude' / 'skills').mkdir(parents=True)
    return tmp_path


@pytest.fixture
def with_fake_gws(monkeypatch):
    """Prepend the fixtures dir to PATH so `gws` resolves to the shim.

    The shim is named `fake_gws.sh`, so we symlink it to `gws` in a
    per-test dir that we prepend.
    """
    # Build a private bin dir holding a `gws` → fake_gws.sh shim
    # (can't rely on user's $TMPDIR since it may conflict with other
    # tests running in parallel).

    def _install(tmpdir: Path, auth: str = 'ok') -> Path:
        bin_dir = tmpdir / 'bin'
        bin_dir.mkdir(parents=True, exist_ok=True)
        link = bin_dir / 'gws'
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(FAKE_GWS)
        monkeypatch.setenv('PATH', f'{bin_dir}:/usr/bin:/bin')
        monkeypatch.setenv('GWS_FAKE_AUTH', auth)
        return bin_dir

    return _install


def _hash_tree(root: Path) -> str:
    """Deterministic hash of a directory tree's file contents."""
    h = hashlib.sha256()
    for f in sorted(root.rglob('*')):
        if f.is_file():
            h.update(str(f.relative_to(root)).encode())
            h.update(b'\0')
            h.update(f.read_bytes())
            h.update(b'\0')
    return h.hexdigest()


def _parse_frontmatter(text: str) -> dict:
    """Minimal YAML frontmatter parser (not a real YAML impl)."""
    if not text.startswith('---'):
        return {}
    end = text.find('\n---', 3)
    if end < 0:
        return {}
    out: dict = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if ':' not in line or line.startswith('#'):
            continue
        key, _, val = line.partition(':')
        out[key.strip()] = val.strip().strip('"')
    return out


# ── check_status() ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_status_no_binary(monkeypatch):
    """Empty PATH → binary=False, auth=False."""
    monkeypatch.setenv('PATH', '')
    status = await gws_integration.check_status()
    assert status['binary'] is False
    assert status['auth'] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_status_not_authenticated(with_fake_gws, tmp_path):
    """auth_method=none → auth=False, reason=not_logged_in."""
    with_fake_gws(tmp_path, auth='fail')
    status = await gws_integration.check_status()
    assert status['binary'] is True
    assert status['auth'] is False
    assert status['auth_reason'] == 'not_logged_in'
    assert status['detail'].get('needs_login') is True
    assert status['version'] and '0.22.5' in status['version']


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_status_all_ok(with_fake_gws, tmp_path):
    """Decryptable creds → auth=True + rich detail for the UI."""
    with_fake_gws(tmp_path, auth='ok')
    status = await gws_integration.check_status()
    assert status['binary'] is True
    assert status['auth'] is True
    assert status['auth_reason'] == 'logged_in'
    assert status['version'] == 'gws 0.22.5 (fake)'
    assert status['detail']['auth_method'] == 'oauth2'
    assert status['detail']['project_id'] == 'fake-project'
    # client_id is preferred over config_client_id when present
    assert status['detail']['account_hint'] == '51610964...com'
    assert status['detail']['storage'] == 'encrypted'


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_status_encryption_invalid(with_fake_gws, tmp_path):
    """Encrypted creds exist but can't decrypt → auth=False + needs_relogin.

    This is the case Ian hit in prod: `gws auth status` still
    exits 0 and reports `auth_method=oauth2`, but the CLI can't
    actually use the keyring on this machine. Old check_status
    would have called this "authenticated" and led the user into
    the install flow with doomed credentials.
    """
    with_fake_gws(tmp_path, auth='encryption_bad')
    status = await gws_integration.check_status()
    assert status['binary'] is True
    assert status['auth'] is False
    assert status['auth_reason'] == 'encryption_invalid'
    assert status['detail']['needs_relogin'] is True
    assert (
        status['detail']['encryption_error']
        == 'Could not decrypt. May have been created on a different machine.'
    )


# ── install_skills() ───────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_passes_relative_output_dir(
    patched_workspace, with_fake_gws, tmp_path
):
    """Real `gws >=0.22` rejects absolute --output-dir (validation error).

    Regression guard: prior to this fix we passed the absolute
    tempdir from ``tempfile.TemporaryDirectory`` directly, which
    caused ``generate-skills`` to exit non-zero. fake_gws.sh now
    enforces the same validation, so an install with an absolute
    --output-dir would surface here as ``RuntimeError``.
    """
    with_fake_gws(tmp_path, auth='ok')
    # Simply completing install_skills without raising is the
    # assertion — fake_gws.sh will exit 2 with the validation error
    # if we regress back to passing an absolute path.
    await gws_integration.install_skills()
    gws_dir = patched_workspace / '.claude' / 'skills' / 'gws'
    assert gws_dir.is_dir(), 'install did not produce the gws/ dir'


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_subset_filter(
    patched_workspace, with_fake_gws, tmp_path
):
    """Only GWS_SUBSET dirs reach disk; junk (gws-chat, persona-hr,
    recipe-backup-sheet, gws-meet) is filtered out."""
    with_fake_gws(tmp_path, auth='ok')
    await gws_integration.install_skills()

    gws_dir = patched_workspace / '.claude' / 'skills' / 'gws'
    installed = {p.name for p in gws_dir.iterdir() if p.is_dir()}
    expected = set(gws_integration.GWS_SUBSET.values())
    assert installed == expected, (
        f'extras: {installed - expected}, missing: {expected - installed}'
    )
    assert (gws_dir / 'SKILL.md').is_file(), 'umbrella missing'

    # Junk must not appear anywhere
    for junk in ('gws-chat', 'gws-meet', 'persona-hr', 'recipe-backup-sheet'):
        assert not (gws_dir / junk).exists()
        # And not with the prefix stripped either
        stripped = junk.removeprefix('gws-')
        if stripped != junk:
            assert not (gws_dir / stripped).exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_cross_ref_rewrite(
    patched_workspace, with_fake_gws, tmp_path
):
    """Every SKILL.md body uses `../<x>/SKILL.md` (no `gws-` prefix)."""
    with_fake_gws(tmp_path, auth='ok')
    await gws_integration.install_skills()
    gws_dir = patched_workspace / '.claude' / 'skills' / 'gws'

    stale_re = re.compile(r'\.\./gws-[a-z0-9][a-z0-9-]*/SKILL\.md')
    for md in gws_dir.rglob('SKILL.md'):
        body = md.read_text()
        matches = stale_re.findall(body)
        assert not matches, (
            f'stale cross-refs in {md.relative_to(gws_dir)}: {matches}'
        )

    # And the rewrite landed — sheets should now reference ../shared/
    sheets_body = (gws_dir / 'sheets' / 'SKILL.md').read_text()
    assert '../shared/SKILL.md' in sheets_body


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_no_prefix_leak_in_paths(
    patched_workspace, with_fake_gws, tmp_path
):
    """No path-shaped `gws-*/SKILL.md` reference left in any sub-skill."""
    with_fake_gws(tmp_path, auth='ok')
    await gws_integration.install_skills()
    gws_dir = patched_workspace / '.claude' / 'skills' / 'gws'

    # Only check sub-skills (the umbrella intentionally has no
    # prefixed paths). We match the path-shape `X/SKILL.md` where X
    # starts with gws-.
    path_re = re.compile(r'[`"/ ]gws-[a-z0-9][a-z0-9-]*/SKILL\.md')
    for md in gws_dir.rglob('SKILL.md'):
        if md.parent == gws_dir:
            continue  # skip umbrella
        body = md.read_text()
        matches = path_re.findall(body)
        assert not matches, (
            f'prefix leak in {md.relative_to(gws_dir)}: {matches}'
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_umbrella_frontmatter(
    patched_workspace, with_fake_gws, tmp_path
):
    with_fake_gws(tmp_path, auth='ok')
    await gws_integration.install_skills()

    umbrella = patched_workspace / '.claude' / 'skills' / 'gws' / 'SKILL.md'
    text = umbrella.read_text()
    fm = _parse_frontmatter(text)
    assert fm['name'] == 'gws'
    assert fm['description'], 'description empty'
    assert 'Bash(gws:*)' in fm.get('allowed-tools', '')


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_catalog_points_at_real_files(
    patched_workspace, with_fake_gws, tmp_path
):
    """Every `./<x>/SKILL.md` row in the umbrella catalog must resolve."""
    with_fake_gws(tmp_path, auth='ok')
    await gws_integration.install_skills()
    gws_dir = patched_workspace / '.claude' / 'skills' / 'gws'
    body = (gws_dir / 'SKILL.md').read_text()

    ref_re = re.compile(r'\./([a-z0-9][a-z0-9-]*)/SKILL\.md')
    refs = set(ref_re.findall(body))
    assert refs, 'umbrella has no sub-skill references'
    for ref in refs:
        assert (gws_dir / ref / 'SKILL.md').is_file(), (
            f'catalog references missing sub-skill: {ref}'
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_catalog_covers_subset(
    patched_workspace, with_fake_gws, tmp_path
):
    """Every destination subdir (except 'shared') shows up in the catalog."""
    with_fake_gws(tmp_path, auth='ok')
    await gws_integration.install_skills()
    gws_dir = patched_workspace / '.claude' / 'skills' / 'gws'
    body = (gws_dir / 'SKILL.md').read_text()

    ref_re = re.compile(r'\./([a-z0-9][a-z0-9-]*)/SKILL\.md')
    catalog = set(ref_re.findall(body))

    # 'shared' appears in its dedicated callout row — still captured
    # by the same regex.
    assert catalog >= set(gws_integration.GWS_SUBSET.values())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_atomic_replace(
    patched_workspace, with_fake_gws, tmp_path
):
    """Re-running install wipes stale sentinels and leaves no temp dirs."""
    with_fake_gws(tmp_path, auth='ok')
    await gws_integration.install_skills()

    gws_dir = patched_workspace / '.claude' / 'skills' / 'gws'
    sentinel = gws_dir / 'sentinel.txt'
    sentinel.write_text('stale')

    await gws_integration.install_skills()

    assert not sentinel.exists(), 'stale sentinel survived reinstall'
    # No leftover staging / backup dirs
    skills_dir = patched_workspace / '.claude' / 'skills'
    leftovers = [
        p.name
        for p in skills_dir.iterdir()
        if p.name.startswith(('.tmp_gws_', '.bak_gws'))
    ]
    assert leftovers == [], f'leftover tmp dirs: {leftovers}'


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_idempotent(patched_workspace, with_fake_gws, tmp_path):
    """Two consecutive installs produce byte-identical file trees."""
    with_fake_gws(tmp_path, auth='ok')
    await gws_integration.install_skills()
    gws_dir = patched_workspace / '.claude' / 'skills' / 'gws'
    h1 = _hash_tree(gws_dir)

    await gws_integration.install_skills()
    h2 = _hash_tree(gws_dir)
    assert h1 == h2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_preserves_prose_mentioning_gws_sheets(
    patched_workspace, with_fake_gws, tmp_path
):
    """Prose like 'the gws-sheets skill' (not a Markdown path) is NOT
    rewritten. Only `../gws-X/SKILL.md` path-shapes get touched."""
    with_fake_gws(tmp_path, auth='ok')
    await gws_integration.install_skills()
    sheets = (
        patched_workspace / '.claude' / 'skills' / 'gws' / 'sheets' / 'SKILL.md'
    )
    body = sheets.read_text()
    # fake_gws.sh plants this prose to guard against over-rewriting
    assert 'the gws-sheets skill is the main one' in body


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_is_installed_reports_true(
    patched_workspace, with_fake_gws, tmp_path
):
    assert gws_integration.is_installed() is False
    with_fake_gws(tmp_path, auth='ok')
    await gws_integration.install_skills()
    assert gws_integration.is_installed() is True


# ── rollback on mid-swap failure ───────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_rollback_restores_prior_install_on_swap_failure(
    patched_workspace, with_fake_gws, tmp_path, monkeypatch
):
    """If the final `staging → gws/` rename fails after we already
    moved the prior gws/ to .bak_gws, the rollback must restore
    .bak_gws back to gws/ so we don't lose the user's existing install.
    """
    with_fake_gws(tmp_path, auth='ok')
    # First install succeeds and leaves a sentinel we can verify
    # got preserved.
    await gws_integration.install_skills()
    gws_dir = patched_workspace / '.claude' / 'skills' / 'gws'
    sentinel = gws_dir / 'SENTINEL_PRIOR_INSTALL.txt'
    sentinel.write_text('prior')

    # Make the second `staging.rename(gws_dir)` blow up AFTER
    # backup has been created.
    original_rename = Path.rename
    calls = {'n': 0}

    def flaky_rename(self, target):
        calls['n'] += 1
        # The sequence of renames under the try block:
        #  1. gws_dir.rename(backup)   -- we let this succeed
        #  2. staging.rename(gws_dir)  -- inject failure here
        if calls['n'] == 2:
            raise OSError('simulated swap failure')
        return original_rename(self, target)

    monkeypatch.setattr(Path, 'rename', flaky_rename)

    with pytest.raises(OSError, match='simulated swap failure'):
        await gws_integration.install_skills()

    # gws_dir must still exist with the prior sentinel intact
    assert gws_dir.exists(), 'rollback did not restore prior install'
    assert sentinel.is_file(), 'prior sentinel lost during failed swap'
    assert sentinel.read_text() == 'prior'
    # No stray backup or staging dirs left behind
    skills = patched_workspace / '.claude' / 'skills'
    leftovers = [
        p.name
        for p in skills.iterdir()
        if p.name.startswith(('.tmp_gws_', '.bak_gws'))
    ]
    assert leftovers == []


# ── uninstall_skills() ─────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_uninstall_wipes(patched_workspace, with_fake_gws, tmp_path):
    """uninstall removes gws/ and leaves siblings untouched."""
    with_fake_gws(tmp_path, auth='ok')
    await gws_integration.install_skills()
    gws_dir = patched_workspace / '.claude' / 'skills' / 'gws'

    # Seed a sibling skill that should survive uninstall
    sibling = patched_workspace / '.claude' / 'skills' / 'browser-use'
    sibling.mkdir()
    (sibling / 'SKILL.md').write_text('# browser-use\n')

    await gws_integration.uninstall_skills()

    assert not gws_dir.exists()
    assert (sibling / 'SKILL.md').read_text() == '# browser-use\n'
    assert gws_integration.is_installed() is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_uninstall_missing_ok(patched_workspace):
    """Uninstalling a never-installed bundle is a no-op, not an error."""
    result = await gws_integration.uninstall_skills()
    assert result == {'removed': False}


# ── regex unit ─────────────────────────────────────────


@pytest.mark.unit
def test_rewrite_regex_scope():
    """_rewrite_cross_refs touches paths, not prose."""
    before = (
        'See `../gws-sheets/SKILL.md` for details. '
        'The gws-sheets skill is the main one. '
        "Don't rewrite gws-sheets-append in prose either."
    )
    after = gws_integration._rewrite_cross_refs(before)
    assert '../sheets/SKILL.md' in after
    assert '../gws-sheets/SKILL.md' not in after
    # Prose untouched
    assert 'The gws-sheets skill is the main one' in after
    assert 'gws-sheets-append in prose' in after
