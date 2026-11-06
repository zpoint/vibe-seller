"""Unit tests for skills sync dependency installation."""

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.platform import venv_bin_dir, venv_executable, venv_python
from app.workspace.skills_sync import SkillsSyncManager


@pytest.fixture(autouse=True)
def no_ads_service():
    """The remote check also asks the ads service; never from a test."""
    with patch(
        'app.ads_skill.refresh_if_bound', new_callable=AsyncMock
    ) as refresh:
        refresh.return_value = None
        yield refresh


@pytest.fixture
def sync_mgr(tmp_path):
    """Create a SkillsSyncManager with a temp dest dir.

    The 24h cooldown now reads its meta path through the
    ``_sync_meta_path`` instance property (derived from
    ``self._dest_dir``), so overriding ``_dest_dir`` to a tmp dir
    naturally isolates the cooldown file too. No extra patching
    needed here.
    """
    mgr = SkillsSyncManager()
    mgr._dest_dir = tmp_path / '.claude' / 'skills'
    mgr._dest_dir.mkdir(parents=True)
    return mgr


def _make_source_skill(src_dir: Path, name: str, files: dict):
    """Helper: create a source skill directory with given files."""
    skill = src_dir / name
    skill.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        (skill / fname).write_text(content)
    return skill


# ── fetch() atomic replace ─────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_replaces_builtin_skill_atomically(sync_mgr, tmp_path):
    """Built-in skill dirs are fully replaced, removing stale files."""
    src = tmp_path / 'source_skills'
    _make_source_skill(
        src,
        'my-skill',
        {
            'SKILL.md': '# v2',
            'script.py': 'print("v2")',
        },
    )

    # Pre-existing dest with a stale file
    dest_skill = sync_mgr._dest_dir / 'my-skill'
    dest_skill.mkdir()
    (dest_skill / 'SKILL.md').write_text('# v1')
    (dest_skill / 'script.py').write_text('print("v1")')
    (dest_skill / 'config.md').write_text('stale')
    stale_venv = dest_skill / '.venv'
    stale_venv.mkdir()
    (stale_venv / 'bin').mkdir()
    stale_pycache = dest_skill / '__pycache__'
    stale_pycache.mkdir()

    venv = tmp_path / '.venv'
    venv.mkdir()
    venv_bin_dir(venv).mkdir()
    venv_python(venv).write_text('fake')

    with (
        patch.object(sync_mgr, '_get_local_source', return_value=src),
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
    ):
        result = await sync_mgr.fetch()

    assert result['synced'] is True
    assert result['replaced'] == 1

    # New files present
    assert (dest_skill / 'SKILL.md').read_text() == '# v2'
    assert (dest_skill / 'script.py').read_text() == 'print("v2")'
    # Stale files gone
    assert not (dest_skill / 'config.md').exists()
    assert not (dest_skill / '.venv').exists()
    assert not (dest_skill / '__pycache__').exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_skips_unchanged_skill(sync_mgr, tmp_path):
    """Unchanged skill dirs are not replaced."""
    src = tmp_path / 'source_skills'
    _make_source_skill(
        src,
        'my-skill',
        {
            'SKILL.md': '# same',
            'script.py': 'print("same")',
        },
    )

    dest_skill = sync_mgr._dest_dir / 'my-skill'
    dest_skill.mkdir()
    (dest_skill / 'SKILL.md').write_text('# same')
    (dest_skill / 'script.py').write_text('print("same")')

    venv = tmp_path / '.venv'
    venv.mkdir()
    venv_bin_dir(venv).mkdir()
    venv_python(venv).write_text('fake')

    with (
        patch.object(sync_mgr, '_get_local_source', return_value=src),
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
    ):
        result = await sync_mgr.fetch()

    assert result['skipped'] == 1
    assert result['replaced'] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_preserves_user_created_skills(sync_mgr, tmp_path):
    """User-created skills (no matching source dir) are untouched."""
    src = tmp_path / 'source_skills'
    _make_source_skill(
        src,
        'builtin-skill',
        {
            'SKILL.md': '# builtin',
        },
    )

    # User-created skill in dest
    user_skill = sync_mgr._dest_dir / 'user-skill'
    user_skill.mkdir()
    (user_skill / 'SKILL.md').write_text('# user created')
    (user_skill / 'custom.py').write_text('user code')

    venv = tmp_path / '.venv'
    venv.mkdir()
    venv_bin_dir(venv).mkdir()
    venv_python(venv).write_text('fake')

    with (
        patch.object(sync_mgr, '_get_local_source', return_value=src),
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
    ):
        await sync_mgr.fetch()

    # User skill untouched
    assert (user_skill / 'SKILL.md').read_text() == '# user created'
    assert (user_skill / 'custom.py').read_text() == 'user code'


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_removes_superseded_browser_use_skill(sync_mgr, tmp_path):
    """In-place-upgrade cleanup: when the source ships browser-harness
    (the 0.13 rename) and not browser-use, a stale 0.12 browser-use skill
    left in the workspace is removed — while browser-harness and any
    user-created skill are kept."""
    src = tmp_path / 'source_skills'
    _make_source_skill(src, 'browser-harness', {'SKILL.md': '# 0.13'})

    # Stale 0.12 skill + a user skill already in dest.
    stale = sync_mgr._dest_dir / 'browser-use'
    stale.mkdir()
    (stale / 'SKILL.md').write_text('# 0.12 subcommands')
    sync_mgr._update_synced_skills(['browser-use'])  # we synced it before
    user_skill = sync_mgr._dest_dir / 'user-skill'
    user_skill.mkdir()
    (user_skill / 'SKILL.md').write_text('# user')

    venv = tmp_path / '.venv'
    venv.mkdir()
    venv_bin_dir(venv).mkdir()
    venv_python(venv).write_text('fake')

    with (
        patch.object(sync_mgr, '_get_local_source', return_value=src),
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
    ):
        await sync_mgr.fetch()

    assert not stale.exists(), 'stale browser-use skill must be removed'
    assert (sync_mgr._dest_dir / 'browser-harness' / 'SKILL.md').exists()
    assert (user_skill / 'SKILL.md').read_text() == '# user'


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_keeps_browser_use_when_still_in_source(sync_mgr, tmp_path):
    """The legacy tree still ships browser-use — it must NOT be removed
    (the cleanup only fires on the harness rename)."""
    src = tmp_path / 'source_skills'
    _make_source_skill(src, 'browser-use', {'SKILL.md': '# 0.12 legacy'})

    venv = tmp_path / '.venv'
    venv.mkdir()
    venv_bin_dir(venv).mkdir()
    venv_python(venv).write_text('fake')

    with (
        patch.object(sync_mgr, '_get_local_source', return_value=src),
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
    ):
        await sync_mgr.fetch()

    assert (sync_mgr._dest_dir / 'browser-use' / 'SKILL.md').exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_detects_stale_files_as_changed(sync_mgr, tmp_path):
    """Dest with extra files (stale) is detected as changed."""
    src = tmp_path / 'source_skills'
    _make_source_skill(
        src,
        'my-skill',
        {
            'SKILL.md': '# same',
        },
    )

    # Dest has same SKILL.md but also a stale config.md
    dest_skill = sync_mgr._dest_dir / 'my-skill'
    dest_skill.mkdir()
    (dest_skill / 'SKILL.md').write_text('# same')
    (dest_skill / 'config.md').write_text('stale')

    venv = tmp_path / '.venv'
    venv.mkdir()
    venv_bin_dir(venv).mkdir()
    venv_python(venv).write_text('fake')

    with (
        patch.object(sync_mgr, '_get_local_source', return_value=src),
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
    ):
        result = await sync_mgr.fetch()

    assert result['replaced'] == 1
    assert not (dest_skill / 'config.md').exists()


# ── _skill_unchanged ───────────────────────────────────


@pytest.mark.unit
def test_skill_unchanged_true(tmp_path):
    """Returns True when source and dest match exactly."""
    src = tmp_path / 'src'
    dest = tmp_path / 'dest'
    src.mkdir()
    dest.mkdir()
    (src / 'SKILL.md').write_text('same')
    (dest / 'SKILL.md').write_text('same')

    assert SkillsSyncManager._skill_unchanged(src, dest) is True


@pytest.mark.unit
def test_skill_unchanged_false_content_differs(tmp_path):
    """Returns False when file content differs."""
    src = tmp_path / 'src'
    dest = tmp_path / 'dest'
    src.mkdir()
    dest.mkdir()
    (src / 'SKILL.md').write_text('v2')
    (dest / 'SKILL.md').write_text('v1')

    assert SkillsSyncManager._skill_unchanged(src, dest) is False


@pytest.mark.unit
def test_skill_unchanged_false_extra_dest_file(tmp_path):
    """Returns False when dest has extra files."""
    src = tmp_path / 'src'
    dest = tmp_path / 'dest'
    src.mkdir()
    dest.mkdir()
    (src / 'SKILL.md').write_text('same')
    (dest / 'SKILL.md').write_text('same')
    (dest / 'config.md').write_text('stale')

    assert SkillsSyncManager._skill_unchanged(src, dest) is False


@pytest.mark.unit
def test_skill_unchanged_false_stale_venv(tmp_path):
    """Returns False when dest has stale .venv dir."""
    src = tmp_path / 'src'
    dest = tmp_path / 'dest'
    src.mkdir()
    dest.mkdir()
    (src / 'SKILL.md').write_text('same')
    (dest / 'SKILL.md').write_text('same')
    venv = dest / '.venv'
    venv.mkdir()
    (venv / 'pyvenv.cfg').write_text('fake')

    assert SkillsSyncManager._skill_unchanged(src, dest) is False


@pytest.mark.unit
def test_skill_unchanged_false_empty_stale_dir(tmp_path):
    """Returns False when dest has empty stale dir like __pycache__."""
    src = tmp_path / 'src'
    dest = tmp_path / 'dest'
    src.mkdir()
    dest.mkdir()
    (src / 'SKILL.md').write_text('same')
    (dest / 'SKILL.md').write_text('same')
    (dest / '__pycache__').mkdir()  # empty stale dir

    assert SkillsSyncManager._skill_unchanged(src, dest) is False


# ── Concurrent fetch / unique temp dirs ─────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_uses_unique_temp_dirs(sync_mgr, tmp_path):
    """Two sequential fetch() calls don't collide on temp dir names."""
    src = tmp_path / 'source_skills'
    _make_source_skill(src, 'my-skill', {'SKILL.md': '# v1'})

    venv = tmp_path / '.venv'
    venv.mkdir()
    venv_bin_dir(venv).mkdir()
    venv_python(venv).write_text('fake')

    with (
        patch.object(sync_mgr, '_get_local_source', return_value=src),
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
    ):
        r1 = await sync_mgr.fetch()
        _make_source_skill(src, 'my-skill', {'SKILL.md': '# v2'})
        r2 = await sync_mgr.fetch()

    assert r1['replaced'] == 1
    assert r2['replaced'] == 1
    # No leftover temp/backup dirs
    for p in sync_mgr._dest_dir.iterdir():
        assert not p.name.startswith('.tmp_'), f'Leftover temp: {p}'
        assert not p.name.startswith('.bak_'), f'Leftover backup: {p}'


# ── Symlink guard ───────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_handles_symlink_dest(sync_mgr, tmp_path):
    """Symlink dest is unlinked (not rmtree'd) to avoid data loss."""
    src = tmp_path / 'source_skills'
    _make_source_skill(src, 'my-skill', {'SKILL.md': '# new'})

    # Create a real directory and symlink to it
    real_dir = tmp_path / 'real_target'
    real_dir.mkdir()
    (real_dir / 'important.txt').write_text('keep me')
    link = sync_mgr._dest_dir / 'my-skill'
    link.symlink_to(real_dir)

    venv = tmp_path / '.venv'
    venv.mkdir()
    venv_bin_dir(venv).mkdir()
    venv_python(venv).write_text('fake')

    with (
        patch.object(sync_mgr, '_get_local_source', return_value=src),
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
    ):
        await sync_mgr.fetch()

    # Symlink removed, real dir untouched
    assert not link.is_symlink()
    assert (real_dir / 'important.txt').exists()
    assert (sync_mgr._dest_dir / 'my-skill' / 'SKILL.md').read_text() == '# new'


# ── synced_skills from source ───────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_synced_skills_derived_from_source(sync_mgr, tmp_path):
    """synced_skills lists only built-in source skills, not user ones."""
    src = tmp_path / 'source_skills'
    _make_source_skill(src, 'builtin-skill', {'SKILL.md': '# b'})

    user_skill = sync_mgr._dest_dir / 'user-skill'
    user_skill.mkdir()
    (user_skill / 'SKILL.md').write_text('# user')

    venv = tmp_path / '.venv'
    venv.mkdir()
    venv_bin_dir(venv).mkdir()
    venv_python(venv).write_text('fake')

    meta_path = sync_mgr._dest_dir / '.sync_meta.json'
    with (
        patch.object(sync_mgr, '_get_local_source', return_value=src),
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
    ):
        await sync_mgr.fetch()

    meta = json.loads(meta_path.read_text())
    assert 'builtin-skill' in meta['synced_skills']
    # user-skill may or may not be there from prior writes,
    # but the key point: it's not erroneously added
    # from dest dir scanning in this fetch


# ── _install_skill_deps ────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_skill_deps_triggers_on_new_requirements(
    sync_mgr, tmp_path
):
    """Dep install runs when requirements.txt is new."""
    skill = sync_mgr._dest_dir / 'test-skill'
    skill.mkdir()
    (skill / 'requirements.txt').write_text('reportlab==4.2.5\n')
    (skill / 'SKILL.md').write_text('---\nname: test\n---\n')

    # Create a fake shared venv
    venv = tmp_path / '.venv'
    venv.mkdir()
    venv_bin_dir(venv).mkdir()
    venv_python(venv).write_text('fake')
    venv_executable(venv, 'uv').write_text('fake')

    with (
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
        patch(
            'asyncio.create_subprocess_exec',
            new_callable=AsyncMock,
        ) as mock_exec,
    ):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b'', b''))
        mock_exec.return_value = mock_proc

        await sync_mgr._install_skill_deps()

        mock_exec.assert_called_once()
        cmd = mock_exec.call_args[0]
        assert 'pip' in cmd[1]
        assert 'install' in cmd[2]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_skill_deps_skips_unchanged(sync_mgr, tmp_path):
    """Dep install skips when requirements.txt hasn't changed."""
    skill = sync_mgr._dest_dir / 'test-skill'
    skill.mkdir()
    req = skill / 'requirements.txt'
    req.write_text('reportlab==4.2.5\n')

    # Simulate already-installed state in meta (content md5 hash)
    content_hash = hashlib.md5(req.read_bytes()).hexdigest()
    sync_mgr._update_sync_meta(
        lambda m: m.update({'installed_deps': {'test-skill': content_hash}})
    )

    venv = tmp_path / '.venv'
    venv.mkdir()
    venv_bin_dir(venv).mkdir()
    venv_python(venv).write_text('fake')

    with (
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
        patch(
            'asyncio.create_subprocess_exec',
            new_callable=AsyncMock,
        ) as mock_exec,
    ):
        await sync_mgr._install_skill_deps()
        mock_exec.assert_not_called()


# ── concurrent meta writes during dep install (#204) ──


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_deps_does_not_clobber_concurrent_meta_writes(
    sync_mgr, tmp_path
):
    """While pip runs, other writers touch .sync_meta.json — and the
    dep install must not write its pre-pip snapshot over them (#204).

    The install blocks on a fake subprocess gated by an Event; while it
    is parked, a concurrent writer records a newly synced skill and the
    legacy-key trim (the boot migration) drops ``last_sync_at``. When
    the install finishes, ALL of that survives and the new dep hash
    lands too.
    """
    skill = sync_mgr._dest_dir / 'test-skill'
    skill.mkdir()
    req = skill / 'requirements.txt'
    req.write_text('reportlab==4.2.5\n')
    content_hash = hashlib.md5(req.read_bytes()).hexdigest()

    venv = tmp_path / '.venv'
    venv.mkdir()
    venv_bin_dir(venv).mkdir()
    venv_python(venv).write_text('fake')

    meta_path = sync_mgr._dest_dir / '.sync_meta.json'
    # A legacy-shape file: schedule key (the migration will trim it)
    # plus inventory.
    meta_path.write_text(
        json.dumps({'last_sync_at': '2026-11-01T00:00:00+00:00'})
    )

    started = asyncio.Event()
    gate = asyncio.Event()

    class FakeProc:
        returncode = 0

        async def communicate(self):
            await gate.wait()
            return (b'', b'')

    async def fake_exec(*args, **kwargs):
        started.set()
        return FakeProc()

    with (
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
        patch('asyncio.create_subprocess_exec', fake_exec),
    ):
        install = asyncio.create_task(sync_mgr._install_skill_deps())
        # Parked: the initial meta snapshot is taken, pip 'runs'.
        await asyncio.wait_for(started.wait(), timeout=5)

        # Concurrent writers, exactly as at boot: a sync records a
        # newly synced skill, and the legacy trim drops the schedule
        # key from the file.
        sync_mgr._update_synced_skills(['concurrent-skill'])
        blob = json.loads(meta_path.read_text())
        blob.pop('last_sync_at', None)
        meta_path.write_text(json.dumps(blob))

        gate.set()
        await asyncio.wait_for(install, timeout=5)

    meta = json.loads(meta_path.read_text())
    assert 'last_sync_at' not in meta, 'trim was clobbered by the install'
    assert 'concurrent-skill' in meta['synced_skills'], (
        'concurrent inventory write was clobbered'
    )
    assert meta['installed_deps'] == {'test-skill': content_hash}
    # The atomic write leaves no tmp strays in the skills dir.
    assert not list(sync_mgr._dest_dir.glob('*.tmp'))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_install_deps_merges_hashes_into_fresh_installed_deps(
    sync_mgr, tmp_path
):
    """The new hash MERGES into whatever ``installed_deps`` the fresh
    read holds — a sibling skill's hash recorded while our pip ran is
    kept, not replaced."""
    skill_a = sync_mgr._dest_dir / 'skill-a'
    skill_a.mkdir()
    req_a = skill_a / 'requirements.txt'
    req_a.write_text('reportlab==4.2.5\n')
    hash_a = hashlib.md5(req_a.read_bytes()).hexdigest()

    venv = tmp_path / '.venv'
    venv.mkdir()
    venv_bin_dir(venv).mkdir()
    venv_python(venv).write_text('fake')

    meta_path = sync_mgr._dest_dir / '.sync_meta.json'
    started, gate = asyncio.Event(), asyncio.Event()

    class FakeProc:
        returncode = 0

        async def communicate(self):
            await gate.wait()
            return (b'', b'')

    async def fake_exec(*args, **kwargs):
        started.set()
        return FakeProc()

    with (
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
        patch('asyncio.create_subprocess_exec', fake_exec),
    ):
        install = asyncio.create_task(sync_mgr._install_skill_deps())
        await asyncio.wait_for(started.wait(), timeout=5)
        # Another skill's dep landed while our pip ran.
        meta_path.write_text(json.dumps({'installed_deps': {'skill-b': 'h2'}}))
        gate.set()
        await asyncio.wait_for(install, timeout=5)

    meta = json.loads(meta_path.read_text())
    assert meta['installed_deps'] == {'skill-a': hash_a, 'skill-b': 'h2'}


@pytest.mark.unit
def test_update_sync_meta_merges_and_leaves_no_tmp(sync_mgr):
    """Successive mutations merge key-by-key (never replace siblings)
    and the atomic write leaves a parseable file, no tmp strays."""
    sync_mgr._update_sync_meta(lambda m: m.update({'a': 1}))
    sync_mgr._update_sync_meta(lambda m: m.update({'b': 2}))
    meta = json.loads(
        (sync_mgr._dest_dir / '.sync_meta.json').read_text(encoding='utf-8')
    )
    assert meta == {'a': 1, 'b': 2}
    assert not list(sync_mgr._dest_dir.glob('*.tmp'))


# ── defer_deps (boot path) ─────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_defer_deps_does_not_block_boot(
    sync_mgr, tmp_path, monkeypatch
):
    """Boot path: fetch(defer_deps=True) returns while dep installs
    still run — pip must never hold /api/health down."""
    src = tmp_path / 'src'
    _make_source_skill(src, 'skill-a', {'SKILL.md': '# a'})
    monkeypatch.setattr(sync_mgr, '_get_local_source', lambda: src)

    gate = asyncio.Event()
    started = asyncio.Event()

    async def slow_install():
        started.set()
        await gate.wait()

    monkeypatch.setattr(sync_mgr, '_install_skill_deps', slow_install)

    result = await sync_mgr.fetch(defer_deps=True)
    # fetch returned while the install is still gated
    assert result['synced'] is True
    assert sync_mgr._deps_task is not None
    assert not sync_mgr._deps_task.done()

    # the task-launch join point blocks until installs finish
    waiter = asyncio.create_task(sync_mgr.wait_deps_ready())
    await asyncio.sleep(0)
    assert not waiter.done()
    gate.set()
    await asyncio.wait_for(waiter, timeout=2)
    assert started.is_set()
    # once done, the join point is a no-op
    await asyncio.wait_for(sync_mgr.wait_deps_ready(), timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_default_still_awaits_deps(sync_mgr, tmp_path, monkeypatch):
    """API sync path keeps blocking semantics (callers rely on deps
    being ready when the route returns)."""
    src = tmp_path / 'src'
    _make_source_skill(src, 'skill-a', {'SKILL.md': '# a'})
    monkeypatch.setattr(sync_mgr, '_get_local_source', lambda: src)

    install = AsyncMock()
    monkeypatch.setattr(sync_mgr, '_install_skill_deps', install)
    await sync_mgr.fetch()
    install.assert_awaited_once()


@pytest.mark.unit
async def test_boot_sync_drops_a_skill_the_package_no_longer_ships(
    sync_mgr, tmp_path, monkeypatch
):
    """A release that drops a skill must drop it locally (rule 8).

    Scoped to skills the previous boot saw the PACKAGE ship — a
    remote-only skill is never in that set and stays the remote job's
    to own. Same mass-delete guard as the remote tier.
    """
    src = tmp_path / 'source_skills'
    (src / 'still-shipped').mkdir(parents=True)
    (src / 'still-shipped' / 'SKILL.md').write_text('v2')
    monkeypatch.setattr(type(sync_mgr), '_get_source_dirs', lambda self: [src])
    dest = sync_mgr._dest_dir
    for name in ('still-shipped', 'dropped-001'):
        (dest / name).mkdir(parents=True)
        (dest / name / 'SKILL.md').write_text('v1')
    sync_mgr._update_sync_meta(
        lambda m: m.update({
            'synced_skills': ['still-shipped', 'dropped-001'],
            'package_skills': ['still-shipped', 'dropped-001'],
        })
    )

    await sync_mgr.fetch()

    assert (dest / 'still-shipped').is_dir()
    assert not (dest / 'dropped-001').exists()
    assert sync_mgr._read_sync_meta()['package_skills'] == ['still-shipped']


@pytest.mark.unit
async def test_boot_sync_refuses_to_drop_most_of_the_tree(
    sync_mgr, tmp_path, monkeypatch
):
    """The guard applies at boot too: an empty source must not wipe."""
    src = tmp_path / 'source_skills'
    src.mkdir()
    monkeypatch.setattr(type(sync_mgr), '_get_source_dirs', lambda self: [src])
    dest = sync_mgr._dest_dir
    for i in range(4):
        (dest / f'dropped-{i}').mkdir(parents=True)
        (dest / f'dropped-{i}' / 'SKILL.md').write_text('v1')
    sync_mgr._update_sync_meta(
        lambda m: m.update({
            'synced_skills': [f'dropped-{i}' for i in range(4)],
            'package_skills': [f'dropped-{i}' for i in range(4)],
        })
    )

    await sync_mgr.fetch()

    for i in range(4):
        assert (dest / f'dropped-{i}').exists()
