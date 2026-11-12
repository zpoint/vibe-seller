"""Unit tests for the mirror semantics of a synced skill tree.

The contract these pin: synced content mirrors upstream — what
upstream drops goes away — and NOTHING else does. Plus the guard that
makes a truncated or hostile manifest unable to wipe a tree, and the
restart precision (only a changed gate FILE needs one).
"""

import httpx
import pytest

from app.sync import github as github_probe
from app.workspace.skills_sync import SkillsSyncManager
from tests.fixtures.fake_github import FakeGitHub

pytestmark = pytest.mark.unit

RAW = 'http://fake/raw/app/skills_v2'


class Manager(SkillsSyncManager):
    """A manager over a tmp tree with a tmp "package" source."""

    def __init__(self, root, package):
        super().__init__()
        self._dest_dir = root / '.claude' / 'skills'
        self._package = package

    def _get_source_dirs(self):
        return [self._package] if self._package.is_dir() else []

    def _get_local_source(self):
        return self._package if self._package.is_dir() else None


@pytest.fixture
def fake():
    return FakeGitHub()


@pytest.fixture
def mgr(tmp_path, monkeypatch, fake):
    package = tmp_path / 'package'
    package.mkdir()
    monkeypatch.setattr('app.workspace.skills_sync.SKILLS_REPO_URL', RAW)
    monkeypatch.setattr(github_probe, 'GITHUB_API_BASE', 'http://fake/api')
    return Manager(tmp_path / 'home', package)


def plant(manager, rel: str, text: str = 'x') -> None:
    path = manager._dest_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def ship_package(manager, rel: str, text: str = 'p') -> None:
    path = manager._package / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def manifest(fake: FakeGitHub, *rels: str) -> None:
    fake.set_manifest('app/skills_v2', list(rels))
    for rel in rels:
        fake.add_tree('app/skills_v2', {rel: f'upstream:{rel}'})


async def sync_with(mgr, fake, monkeypatch):
    """One remote sync over the fake's transport."""
    async with fake.client() as client:
        return await mgr.do_remote_sync(client)


# ── deletion inside a synced skill ────────────────────────────────


async def test_a_file_removed_upstream_goes_locally(mgr, fake, monkeypatch):
    plant(mgr, 'widget-006/SKILL.md', 'old')
    plant(mgr, 'widget-006/EXTRA.md', 'extra')
    mgr._update_synced_skills(['widget-006'])
    manifest(fake, 'widget-006/SKILL.md')

    result = await sync_with(mgr, fake, monkeypatch)

    assert result['status'] == 'success'
    assert result['deleted'] == 1
    assert (mgr._dest_dir / 'widget-006' / 'SKILL.md').read_text() == (
        'upstream:widget-006/SKILL.md'
    )
    assert not (mgr._dest_dir / 'widget-006' / 'EXTRA.md').exists()


async def test_a_whole_skill_removed_upstream_is_dropped(
    mgr, fake, monkeypatch
):
    plant(mgr, 'widget-006/SKILL.md')
    plant(mgr, 'other-001/SKILL.md')
    mgr._update_synced_skills(['widget-006', 'other-001'])
    manifest(fake, 'other-001/SKILL.md')

    result = await sync_with(mgr, fake, monkeypatch)

    assert result['deleted'] == 1
    assert not (mgr._dest_dir / 'widget-006').exists()
    assert (mgr._dest_dir / 'other-001').is_dir()
    assert 'widget-006' not in mgr.get_synced_skills()
    assert 'other-001' in mgr.get_synced_skills()


async def test_what_sync_does_not_own_is_never_touched(mgr, fake, monkeypatch):
    plant(mgr, 'user-made/SKILL.md', 'mine')
    plant(mgr, 'amazon-ads-api/SKILL.md', 'from the ads service')
    plant(mgr, 'gws-bundle/SKILL.md', 'installed bundle')
    manifest(fake, 'widget-006/SKILL.md')

    await sync_with(mgr, fake, monkeypatch)

    assert (mgr._dest_dir / 'user-made' / 'SKILL.md').read_text() == 'mine'
    assert (mgr._dest_dir / 'amazon-ads-api' / 'SKILL.md').exists()
    assert (mgr._dest_dir / 'gws-bundle' / 'SKILL.md').exists()


async def test_a_package_file_missing_from_the_manifest_is_kept(
    mgr, fake, monkeypatch
):
    ship_package(mgr, 'widget-006/SKILL.md', 'package version')
    plant(mgr, 'widget-006/SKILL.md', 'package version')
    mgr._update_synced_skills(['widget-006'])
    manifest(fake, 'widget-006/EXTRA.md')

    result = await sync_with(mgr, fake, monkeypatch)

    assert result['deleted'] == 0
    assert (mgr._dest_dir / 'widget-006' / 'SKILL.md').read_text() == (
        'package version'
    )
    assert (mgr._dest_dir / 'widget-006' / 'EXTRA.md').exists()


# ── the guards ────────────────────────────────────────────────────


async def test_a_broken_manifest_deletes_nothing(mgr, fake, monkeypatch):
    plant(mgr, 'widget-006/SKILL.md', 'keep me')
    mgr._update_synced_skills(['widget-006'])

    # 1. manifest fetch fails
    fake.set_manifest('app/skills_v2', [])
    result = await sync_with(mgr, fake, monkeypatch)
    assert result['status'] == 'failed'
    assert (mgr._dest_dir / 'widget-006' / 'SKILL.md').exists()

    # 2. manifest is empty
    fake.files['app/skills_v2/MANIFEST.txt'] = b'\n# nothing\n'
    result = await sync_with(mgr, fake, monkeypatch)
    assert result['status'] == 'failed'
    assert 'empty' in result['error']
    assert (mgr._dest_dir / 'widget-006' / 'SKILL.md').exists()


async def test_a_mass_delete_is_refused_and_nothing_changes(
    mgr, fake, monkeypatch
):
    for i in range(4):
        plant(mgr, f'skill-{i}/SKILL.md', f'content {i}')
    mgr._update_synced_skills([f'skill-{i}' for i in range(4)])
    manifest(fake, 'skill-0/SKILL.md')  # drops three of four

    result = await sync_with(mgr, fake, monkeypatch)

    assert result['status'] == 'failed'
    assert 'mass delete' in result['error']
    assert result['deleted'] == 0
    for i in range(4):
        assert (mgr._dest_dir / f'skill-{i}' / 'SKILL.md').exists()


async def test_a_crash_mid_apply_leaves_the_previous_tree(
    mgr, fake, monkeypatch
):
    plant(mgr, 'widget-006/SKILL.md', 'old')
    mgr._update_synced_skills(['widget-006'])
    manifest(fake, 'widget-006/SKILL.md')

    # Simulate dying between staging and the rename.
    monkeypatch.setattr(
        'app.workspace.skill_mirror.SkillMirrorMixin._swap_in',
        staticmethod(lambda staging, dest: (_ for _ in ()).throw(OSError('x'))),
    )
    result = await sync_with(mgr, fake, monkeypatch)

    assert result['status'] == 'failed'
    assert (mgr._dest_dir / 'widget-006' / 'SKILL.md').read_text() == 'old'
    assert not any(
        p.name.startswith('.tmp_') for p in (mgr._dest_dir).iterdir()
    )


# ── needs_restart precision ───────────────────────────────────────


async def test_a_changed_gate_file_needs_a_restart(mgr, fake, monkeypatch):
    plant(mgr, 'widget-006/SKILL.md', 'old')
    plant(mgr, 'widget-006/gates/ad_completeness_review.py', 'old gate')
    mgr._update_synced_skills(['widget-006'])
    manifest(
        fake,
        'widget-006/SKILL.md',
        'widget-006/gates/ad_completeness_review.py',
    )

    result = await sync_with(mgr, fake, monkeypatch)
    assert result['needs_restart'] is True


async def test_a_non_gate_change_in_a_gated_skill_needs_no_restart(
    mgr, fake, monkeypatch
):
    plant(mgr, 'widget-006/SKILL.md', 'old')
    plant(mgr, 'widget-006/gates/ad_completeness_review.py', 'gate')
    mgr._update_synced_skills(['widget-006'])
    # The gate file is still upstream (unchanged); only the doc moves.
    manifest(
        fake,
        'widget-006/SKILL.md',
        'widget-006/gates/ad_completeness_review.py',
    )
    fake.add_tree(
        'app/skills_v2',
        {'widget-006/gates/ad_completeness_review.py': 'gate'},
    )

    result = await sync_with(mgr, fake, monkeypatch)
    assert result['needs_restart'] is False


async def test_removing_a_gated_skill_needs_a_restart(mgr, fake, monkeypatch):
    plant(mgr, 'widget-006/SKILL.md')
    plant(mgr, 'widget-006/gates/ad_completeness_review.py', 'gate')
    mgr._update_synced_skills(['widget-006'])
    manifest(fake, 'other-001/SKILL.md')

    result = await sync_with(mgr, fake, monkeypatch)
    assert result['deleted'] == 1
    assert result['needs_restart'] is True


async def test_a_failed_download_never_deletes_the_local_file(
    mgr, fake, monkeypatch
):
    """A transient fetch error is not an upstream deletion.

    The upstream set is what the MANIFEST LISTS, not what happened to
    download: one 500 on one file must not delete the user's copy of a
    file upstream still ships. Nothing is deleted, the failed file
    keeps its bytes, and the run is a failure so ``last_ok_at`` does
    not move and the job retries next cadence.
    """
    plant(mgr, 'widget-006/SKILL.md', 'old skill')
    plant(mgr, 'widget-006/EXTRA.md', 'old extra')
    mgr._update_synced_skills(['widget-006'])
    manifest(fake, 'widget-006/SKILL.md', 'widget-006/EXTRA.md')

    def flaky(request):
        if request.url.path.endswith('EXTRA.md'):
            return httpx.Response(500, text='boom')
        return fake.handler(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(flaky)
    ) as client:
        result = await mgr.do_remote_sync(client)

    assert result['status'] == 'failed'
    assert 'failed to download' in result['error']
    assert result['deleted'] == 0
    # the file that DID download landed ...
    assert (mgr._dest_dir / 'widget-006' / 'SKILL.md').read_text() == (
        'upstream:widget-006/SKILL.md'
    )
    # ... and the one that did not kept its local bytes.
    assert (mgr._dest_dir / 'widget-006' / 'EXTRA.md').read_text() == (
        'old extra'
    )


async def test_a_skill_known_only_by_a_failed_file_is_not_deleted(
    mgr, fake, monkeypatch
):
    """Rule 3: a skill dir counts as upstream if the manifest lists any
    file under it, regardless of download success."""
    plant(mgr, 'widget-006/SKILL.md', 'old')
    mgr._update_synced_skills(['widget-006'])
    manifest(fake, 'widget-006/SKILL.md')

    def flaky(request):
        return httpx.Response(500, text='boom')

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(flaky)
    ) as client:
        result = await mgr.do_remote_sync(client)

    assert result['status'] == 'failed'
    assert (mgr._dest_dir / 'widget-006' / 'SKILL.md').read_text() == 'old'


# ── boot sweep: residue of interrupted installs ────────────────────


def test_sweep_clears_import_residue(mgr):
    """A replace-install that died mid-swap leaves .<slug>-import-*
    staging and its …-import-*-old renamed-aside tree INSIDE the
    live skills dir (the swap runs under the skills-tree lock in one
    server process — at boot nothing can still own them). They must
    ride the same boot sweep as the sync strays, or they travel into
    every task-workspace copy forever."""
    skills = mgr._dest_dir
    skills.mkdir(parents=True, exist_ok=True)
    (skills / '.gate-import-abc123').mkdir()
    (skills / '.gate-import-abc123-old').mkdir()
    plant(mgr, 'gate/SKILL.md', 'live')
    (skills / '.tmp_legacy').mkdir()

    mgr.sweep_staging()

    assert not (skills / '.gate-import-abc123').exists()
    assert not (skills / '.gate-import-abc123-old').exists()
    assert not (skills / '.tmp_legacy').exists()
    assert (skills / 'gate' / 'SKILL.md').read_text() == 'live'
