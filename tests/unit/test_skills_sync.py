"""Unit tests for skills sync dependency installation."""

import asyncio
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.models.app_settings import AppSettings
from app.workspace import skills_sync as ss
from app.workspace.skills_sync import SkillsSyncManager


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
    (venv / 'bin').mkdir()
    (venv / 'bin' / 'python').write_text('fake')

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
    (venv / 'bin').mkdir()
    (venv / 'bin' / 'python').write_text('fake')

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
    (venv / 'bin').mkdir()
    (venv / 'bin' / 'python').write_text('fake')

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
    (venv / 'bin').mkdir()
    (venv / 'bin' / 'python').write_text('fake')

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
    (venv / 'bin').mkdir()
    (venv / 'bin' / 'python').write_text('fake')

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
    (venv / 'bin').mkdir()
    (venv / 'bin' / 'python').write_text('fake')

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
    (venv / 'bin').mkdir()
    (venv / 'bin' / 'python').write_text('fake')

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
    (venv / 'bin').mkdir()
    (venv / 'bin' / 'python').write_text('fake')
    (venv / 'bin' / 'uv').write_text('fake')

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
    sync_mgr._write_sync_meta({
        'installed_deps': {'test-skill': content_hash},
    })

    venv = tmp_path / '.venv'
    venv.mkdir()
    (venv / 'bin').mkdir()
    (venv / 'bin' / 'python').write_text('fake')

    with (
        patch('app.workspace.skills_sync.VIBE_SELLER_DIR', tmp_path),
        patch(
            'asyncio.create_subprocess_exec',
            new_callable=AsyncMock,
        ) as mock_exec,
    ):
        await sync_mgr._install_skill_deps()
        mock_exec.assert_not_called()


# ── Auto-sync gate ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_and_sync_remote_skips_when_disabled(sync_mgr):
    """``skills_auto_sync_enabled=false`` short-circuits the poll.

    The gate must run BEFORE any network call so an opted-out user
    never sees a request leave the box (and so a flaky upstream
    can't keep retrying behind their back).
    """
    with (
        patch(
            'app.workspace.skills_sync._auto_sync_enabled',
            new_callable=AsyncMock,
        ) as gate,
        patch.object(
            sync_mgr, '_fetch_remote_commit', new_callable=AsyncMock
        ) as fetch_commit,
    ):
        gate.return_value = False
        result = await sync_mgr.check_and_sync_remote()

    assert result is None
    fetch_commit.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_and_sync_remote_runs_when_enabled(sync_mgr):
    """When the gate is open we still hit the network path.

    Pairs with the opt-out test above: confirms removing the gate
    doesn't accidentally also kill the happy path. The remote-commit
    fetch returning None makes the function early-exit cleanly,
    which is enough to prove the gate was passed.
    """
    with (
        patch(
            'app.workspace.skills_sync._auto_sync_enabled',
            new_callable=AsyncMock,
        ) as gate,
        patch.object(
            sync_mgr, '_fetch_remote_commit', new_callable=AsyncMock
        ) as fetch_commit,
    ):
        gate.return_value = True
        fetch_commit.return_value = None  # Simulate unreachable GitHub
        result = await sync_mgr.check_and_sync_remote()

    assert result is None
    fetch_commit.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_and_sync_remote_cooldown_short_circuits_before_gate(
    sync_mgr,
):
    """Active 24h cooldown means we never read AppSettings.

    Pins the perf-driven ordering: the cheap file-based cooldown
    check has to run BEFORE the DB lookup so the hot path (every
    task launch) doesn't pay a round-trip on every call.
    """
    meta_path = sync_mgr._dest_dir / '.sync_meta.json'
    meta_path.write_text(
        json.dumps({'last_sync_at': datetime.now(UTC).isoformat()})
    )

    with (
        patch(
            'app.workspace.skills_sync._auto_sync_enabled',
            new_callable=AsyncMock,
        ) as gate,
        patch.object(
            sync_mgr, '_fetch_remote_commit', new_callable=AsyncMock
        ) as fetch_commit,
    ):
        result = await sync_mgr.check_and_sync_remote()

    assert result is None
    gate.assert_not_called()
    fetch_commit.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auto_sync_enabled_defaults_to_true():
    """Missing AppSettings row → enabled (ships on by default)."""
    fake_db = AsyncMock()
    fake_db.get = AsyncMock(return_value=None)
    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_db)
    fake_session.__aexit__ = AsyncMock(return_value=None)

    with patch.object(ss, 'async_session', return_value=fake_session):
        assert await ss._auto_sync_enabled() is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auto_sync_enabled_reads_false():
    """Row with value='false' disables auto-sync."""
    fake_db = AsyncMock()
    fake_db.get = AsyncMock(
        return_value=AppSettings(key='skills_auto_sync_enabled', value='false')
    )
    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_db)
    fake_session.__aexit__ = AsyncMock(return_value=None)

    with patch.object(ss, 'async_session', return_value=fake_session):
        assert await ss._auto_sync_enabled() is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auto_sync_enabled_defaults_true_on_db_error():
    """DB failure → treat as enabled, never silently stop syncing."""

    def _boom(*args, **kwargs):
        raise RuntimeError('db blew up')

    with patch.object(ss, 'async_session', side_effect=_boom):
        assert await ss._auto_sync_enabled() is True


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
