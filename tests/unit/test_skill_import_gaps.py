"""Review findings as failing tests: the gaps in the CURRENT skill
import surfaces (worker S review, feeding the task/URL-import PR).

Every test here pins a contract the code SHOULD hold but does not
enforce yet. They are deliberately RED on the reviewed branch: each
one names the design fix the import work must land with.

Covered gaps (see ~/vibe-seller-docs/report-s.md for the full write-up):

* ``create_skill`` has no collision rules at all — it silently
  overwrites an existing skill's SKILL.md (user or BUILT-IN), and
  accepts a name whose slug is empty (``Path(dir) / ''`` is the dir
  itself), planting a stray ``skills/SKILL.md`` + a lockfile entry
  keyed ``''``;
* ``delete_skill`` refuses only ``_``-prefixed slugs, so a maintainer-
  synced built-in (``browser-harness``) can be deleted through the
  public DELETE route / ``manage_skill(action=delete)``;
* the built-in sync takes over a user-imported slug with a silent
  tree swap while the lockfile keeps claiming the skill is
  user-owned (``source=url``) — provenance lies either way;
* archive extraction refuses two entries whose names differ only by
  CASE, but not by Unicode NORMALIZATION (NFC vs NFD) — those
  collide on APFS and diverge on ext4, so one archive installs two
  different trees depending on the host OS.
"""

import io
import json
import unicodedata
import zipfile

import pytest

from app.skill_uploads import extract_archive
from app.workspace.manager import WorkspaceManager
from app.workspace.skills_sync import SkillsSyncManager

pytestmark = pytest.mark.unit

SKILL_MD = '---\nname: gap-skill\ndescription: review gap\n---\n# gap-skill\n'


def _mgr(tmp_path, synced=None):
    """A real WorkspaceManager on a bare tmp workspace (the shape
    tests/unit/test_mcp_skill_import.py uses)."""
    root = tmp_path / 'ws'
    skills_dir = root / '.claude' / 'skills'
    skills_dir.mkdir(parents=True)
    (root / '.git').mkdir()
    if synced:
        (skills_dir / '.sync_meta.json').write_text(
            json.dumps({'synced_skills': synced})
        )
    return WorkspaceManager(root), skills_dir


def _read_lock(skills_dir):
    return json.loads((skills_dir / 'skills.lock.json').read_text())


class TestCreateSkillHasNoCollisionRules:
    async def test_create_refuses_a_name_that_collides_with_a_builtin(
        self, tmp_path
    ):
        """``create_skill`` checks only the ``_`` prefix — a built-in
        slug (``browser-harness``, synced, read-only everywhere else)
        is reachable by casing the name differently: the name→slug
        map folds it. The built-in's SKILL.md must survive."""
        mgr, skills_dir = _mgr(tmp_path, synced=['browser-harness'])
        builtin = skills_dir / 'browser-harness'
        builtin.mkdir()
        (builtin / 'SKILL.md').write_text(
            '---\nname: browser-harness\ndescription: real\n---\n'
            'REAL CLI REFERENCE\n'
        )
        with pytest.raises(ValueError, match='built-in'):
            await mgr.create_skill('Browser Harness')
        assert 'REAL CLI REFERENCE' in (builtin / 'SKILL.md').read_text()

    async def test_create_refuses_an_existing_user_skill(self, tmp_path):
        """The import flow made wholesale replacement of an existing
        USER skill a confirmed act (``overwrite=true``); create needs
        no confirmation at all and its template write silently
        destroys the user's hand-made SKILL.md — the overwrite gate,
        bypassed through another door."""
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.create_skill('revenue-report', description='first')
        hand_written = (skills_dir / 'revenue-report' / 'SKILL.md').read_text()
        with pytest.raises(ValueError, match='exists'):
            await mgr.create_skill('Revenue Report')  # same slug!
        assert (
            skills_dir / 'revenue-report' / 'SKILL.md'
        ).read_text() == hand_written

    async def test_create_rejects_a_name_that_slugifies_to_empty(
        self, tmp_path
    ):
        """``skill_slug('中文技能')`` is ``''`` and ``dir / ''`` is the
        dir itself — the call claims success, writes a stray
        ``skills/SKILL.md`` that no tier of reader expects, and keys
        the lockfile entry ``''``. A Chinese-named skill must fail
        loudly instead."""
        mgr, skills_dir = _mgr(tmp_path)
        with pytest.raises(ValueError, match='slug'):
            await mgr.create_skill('中文技能')
        assert not (skills_dir / 'SKILL.md').exists()
        assert '' not in _read_lock(skills_dir)['skills']


class TestDeleteSkillRefusesOnlyUnderscores:
    async def test_delete_refuses_a_synced_builtin(self, tmp_path):
        """``delete_skill`` gates on the ``_`` prefix, but no synced
        (maintainer-owned, declared read-only) built-in starts with
        ``_`` — DELETE /skills/browser-harness removes it, and it
        stays gone from every task launch until a restart or a remote
        sync run re-materialises it."""
        mgr, skills_dir = _mgr(tmp_path, synced=['browser-harness'])
        builtin = skills_dir / 'browser-harness'
        builtin.mkdir()
        (builtin / 'SKILL.md').write_text('REAL CLI REFERENCE')
        with pytest.raises(ValueError, match='built-in'):
            await mgr.delete_skill('browser-harness')
        assert builtin.is_dir()


class TestSyncTakeoverKeepsLyingLockfile:
    async def test_fetch_takeover_does_not_leave_provenance_claiming_user(
        self, tmp_path, monkeypatch
    ):
        """A release that ships a NEW built-in under a slug some user
        already imported: the boot mirror swaps the user's tree for
        the package tree (mirror semantics) while the lockfile entry
        still records ``source=url`` with the user's ``origin_url``.
        Nothing says the user's copy was superseded — one month later
        ``manage_skill(list)``/UI trust that entry. The takeover must
        at least stop claiming the skill is user-owned."""
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.save_skill(
            'gap-skill', SKILL_MD, origin_url='https://example.test/skill'
        )
        assert _read_lock(skills_dir)['skills']['gap-skill']['source'] == 'url'

        src = tmp_path / 'pkg-skills'
        pkg = src / 'gap-skill'
        pkg.mkdir(parents=True)
        (pkg / 'SKILL.md').write_text(
            '---\nname: gap-skill\ndescription: maintainer now\n---\n'
            'MAINTAINER TREE\n'
        )
        sync_mgr = SkillsSyncManager()
        sync_mgr._dest_dir = skills_dir
        monkeypatch.setattr(sync_mgr, '_get_local_source', lambda: src)
        monkeypatch.setattr(sync_mgr, '_install_skill_deps', _noop)
        await sync_mgr.fetch()

        assert (skills_dir / 'gap-skill' / 'SKILL.md').read_text() == (
            '---\nname: gap-skill\ndescription: maintainer now\n---\n'
            'MAINTAINER TREE\n'
        ), 'the swap itself'
        lock = _read_lock(skills_dir)['skills'].get('gap-skill', {})
        assert lock.get('source') != 'url', (
            'sync replaced the user import with the built-in tree but '
            'left the lockfile asserting it is still a user-owned '
            'import from ' + str(lock.get('origin_url'))
        )


async def _noop(*args, **kwargs):
    """Neutralize the venv pip install — this test is about the tree."""
    return None


class TestExtractionCollisions:
    @staticmethod
    def _zip_with(names_and_blobs):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            for name, blob in names_and_blobs:
                zf.writestr(name, blob)
        return buf.getvalue()

    def test_extract_refuses_names_colliding_by_normalization(self, tmp_path):
        """The extractor already refuses names differing only by CASE
        (they collide on macOS/Windows). NFC vs NFD differ by
        normalization, not case — ``unicodedata.normalize('NFC', 'é')``
        != the NFD bytes — so both entries pass the check and then
        land on ONE APFS file (the second silently wins) or TWO files
        on ext4: the same archive installs a different tree per host.
        Refuse it like the case rule does."""
        base = 'skill/SKILL.md'
        nfc = 'skill/' + unicodedata.normalize('NFC', 'café.md')
        nfd = 'skill/' + unicodedata.normalize('NFD', 'café.md')
        assert nfc != nfd
        assert nfc.lower() != nfd.lower()  # the case rule cannot see it
        data = self._zip_with([
            (base, SKILL_MD),
            (nfc, b'content-A'),
            (nfd, b'content-B'),
        ])
        path = tmp_path / 'collide.zip'
        path.write_bytes(data)
        with pytest.raises(ValueError, match='collide'):
            extract_archive(path, 'zip')

    def test_extract_refuses_a_zip_bomb_of_junk_entries(self, tmp_path):
        """Every junk entry (``._x``/``.DS_Store``/``__MACOSX``) is
        skipped BEFORE the file-count cap and appended to ``ignored``
        unconditionally — a 32 MB archive of ~half a million 60-byte
        junk entries yields a half-million-item ``ignored`` list,
        which the import then serializes straight into the agent's
        tool result. ``ignored`` must be capped (count + sample), not
        accumulated without bound."""
        entries = [('skill/SKILL.md', SKILL_MD.encode())]
        for i in range(20_000):
            entries.append((f'skill/._junk{i}', b'x'))
        path = tmp_path / 'junkflood.zip'
        path.write_bytes(self._zip_with(entries))
        extracted = extract_archive(path, 'zip')
        assert len(extracted.ignored) <= 1000, (
            f'{len(extracted.ignored)} ignored names is unbounded '
            'result payload for the agent'
        )
