"""Mirroring the upstream tree — staging, atomic swaps, deletion.

Split out of ``skills_sync`` to keep that module under the line limit.
Everything here is about ONE question: making a synced tree match what
upstream ships (and nothing else), without any reader ever catching it
half-done.

A :class:`SkillMirrorMixin` is mixed into ``SkillsSyncManager``; ``self``
is the full manager, so these methods use ``self._dest_dir``,
``self._get_source_dirs`` and ``self.get_synced_skills``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import tempfile

logger = logging.getLogger(__name__)

# What the remote tier may never shadow: the installed package is the
# authoritative content for the lifetime of that version.
_PACKAGE_ONLY_SUFFIXES = ('__init__.py', 'MANIFEST.txt')
_COPY_IGNORE = shutil.ignore_patterns('__pycache__', '__init__.py', '.venv')


def _remove(path: Path) -> None:
    """Delete whatever ``path`` is, without following a symlink."""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path, ignore_errors=True)


class SkillMirrorMixin:
    """Staging + mirror semantics for a synced skill tree."""

    @property
    def _staging_root(self) -> Path:
        """Where half-built skill dirs wait for their rename.

        A SIBLING of the skills tree (``.claude/.sync-staging``), never
        inside it: ``.claude/`` is copied wholesale into every task
        workspace, so a leftover ``.tmp_*`` in the skills dir would be
        copied along — and Claude Code would discover it as a second,
        broken copy of the skill. Same filesystem as the destination,
        so the swap is a plain rename.
        """
        return self._dest_dir.parent / '.sync-staging'

    def _make_staging(self, prefix: str) -> Path:
        """A unique empty staging dir beside the skills tree."""
        root = self._staging_root
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(dir=str(root), prefix=prefix))

    def sweep_staging(self) -> None:
        """Drop half-built trees left by an interrupted sync.

        Called at boot, before anything copies the skills tree. Also
        clears the in-tree ``.tmp_*`` / ``.bak_*`` strays that releases
        before this staging home used to leave in the skills dir.
        """
        shutil.rmtree(self._staging_root, ignore_errors=True)
        if not self._dest_dir.is_dir():
            return
        for stale in self._dest_dir.glob('.tmp_*'):
            _remove(stale)
        for stale in self._dest_dir.glob('.bak_*'):
            _remove(stale)

    def _install_file(self, target: Path, content: bytes) -> bool:
        """Atomically write one file into the live tree.

        ``False`` when the file already holds these bytes. Staged
        beside the tree and renamed in, so a reader never sees a
        truncated file (and a crash cannot leave one).
        """
        if target.exists():
            try:
                if target.read_bytes() == content:
                    return False
            except OSError:
                pass
        staging = self._make_staging('file_')
        tmp = staging / 'payload'
        tmp.write_bytes(content)
        os.replace(tmp, target)
        _remove(staging)
        return True

    @staticmethod
    def _swap_in(staging: Path, dest: Path) -> None:
        """Rename ``staging`` over ``dest``.

        Both are on the same filesystem and this runs inside ONE
        synchronous block, so no reader can observe the intermediate
        state. If the rename fails the previous tree is still intact.
        """
        _remove(dest)
        staging.rename(dest)

    @staticmethod
    def _is_gate_path(rel: str) -> bool:
        """Whether this rel path IS gate code (``<skill>/gates/*.py``).

        Restart precision: only a changed gate FILE needs one. A skill
        that merely has gates, being touched somewhere else, does not.
        """
        parts = Path(rel).parts
        return (
            len(parts) >= 3 and parts[1] == 'gates' and parts[2].endswith('.py')
        )

    # ── Local package sync ──────────────────────────────

    def _has_gate_files(self, skill_name: str) -> bool:
        """Whether a skill about to be removed ships gate code."""
        gates = self._dest_dir / skill_name / 'gates'
        return gates.is_dir() and any(gates.glob('*.py'))

    def _package_files(self) -> dict[str, bytes]:
        """Every file the local package (or a plugin source) ships.

        The package is the authority for those paths: remote sync never
        overwrites one, and never deletes one either.
        """
        out: dict[str, bytes] = {}
        for src in self._get_source_dirs():
            for f in sorted(src.rglob('*')):
                if not f.is_file() or f.name.startswith('.'):
                    continue
                if f.name in _PACKAGE_ONLY_SUFFIXES:
                    continue
                if '__pycache__' in f.parts:
                    continue
                out.setdefault(f.relative_to(src).as_posix(), f.read_bytes())
        return out

    def _existing_files(self) -> dict[str, Path]:
        """Every file currently in the synced tree (rel -> path)."""
        out: dict[str, Path] = {}
        if not self._dest_dir.is_dir():
            return out
        for f in sorted(self._dest_dir.rglob('*')):
            if not f.is_file() or f.name.startswith('.'):
                continue
            if '__pycache__' in f.parts or '.venv' in f.parts:
                continue
            out[f.relative_to(self._dest_dir).as_posix()] = f
        return out

    def _apply_downloads(
        self,
        downloads: dict[str, bytes],
        listed: set[str],
        failed: set[str],
    ) -> dict:
        """Make the synced tree match upstream, atomically.

        MIRROR semantics: the desired content of every skill sync owns
        is exactly what upstream ships — the remote MANIFEST plus the
        local package source (the package stays authoritative and is
        never deleted by remote sync). A file or a whole skill that
        upstream dropped is removed here, so synced content cannot
        outlive what it was synced from.

        Scope is everything sync owns and nothing else: skills recorded
        in the inventory (``synced_skills``) and whatever the manifest
        names. User-created skills, the ads service's bundle and the
        gws bundle are not in that set and are never touched.

        Every change is built in the staging root and renamed into
        place (one synchronous block), so a task workspace copy can
        never catch a half-written skill.

        Returns ``{'copied', 'skipped', 'deleted', 'needs_restart',
        'refused'}``; ``refused`` is set when the mass-deletion guard
        trips and NOTHING was changed.
        """
        self._dest_dir.mkdir(parents=True, exist_ok=True)
        package = self._package_files()
        existing = self._existing_files()
        if failed:
            # A listed file we could not fetch is NOT upstream-dropped:
            # the transient 404/500 must not delete the local copy of a
            # file upstream still ships. Nothing is deleted this run and
            # the failed file keeps whatever is on disk.
            logger.warning(
                '%d listed file(s) failed to download; keeping the tree',
                len(failed),
            )
        # What each owned file should end up as. Manifest files are
        # remote's to write; package files are the LOCAL fetch's — remote
        # keeps whatever is on disk (and only materialises one that is
        # missing), so remote can delete what upstream dropped without
        # ever overwriting what the package ships.
        desired: dict[str, bytes] = {}
        for rel in set(package) | listed:
            if rel in downloads:
                desired[rel] = downloads[rel]
                continue
            if rel in package:
                desired[rel] = package[rel]
                continue
            # Listed but not fetched (or fetched and failed): keep the
            # local copy. It is upstream's file either way.
            have = existing.get(rel)
            if have is not None:
                desired[rel] = have.read_bytes()
        synced = self.get_synced_skills()

        # A skill counts as upstream if the MANIFEST lists any file
        # under it — regardless of whether that file downloaded.
        manifest_dirs = {rel.split('/', 1)[0] for rel in listed if '/' in rel}
        upstream_dirs = {rel.split('/', 1)[0] for rel in desired if '/' in rel}
        owned_dirs = synced | upstream_dirs

        # ── what would go away ─────────────────────────────
        doomed_dirs = (
            []
            if failed
            else sorted(d for d in synced if d not in upstream_dirs)
        )
        doomed_files = [
            rel
            for rel in existing
            if not failed
            and '/' in rel
            and rel.split('/', 1)[0] in owned_dirs
            and rel.split('/', 1)[0] not in doomed_dirs
            and rel not in listed
            and rel not in package
        ]
        if self._guard_mass_delete(
            doomed_dirs=doomed_dirs,
            doomed_files=doomed_files,
            owned_dirs=owned_dirs,
            existing=existing,
        ):
            return {
                'copied': 0,
                'skipped': 0,
                'deleted': 0,
                'needs_restart': False,
                'refused': 'refusing mass delete',
            }

        copied = skipped = 0
        needs_restart = False

        # Whole skills upstream dropped: rename away, then delete.
        gated = {name for name in doomed_dirs if self._has_gate_files(name)}
        for name in doomed_dirs:
            doomed = self._dest_dir / name
            staging = self._make_staging(f'{name}_')
            try:
                _remove(staging)
                doomed.rename(staging)
            except OSError:
                logger.warning('could not stage %s for removal', name)
                continue
            _remove(staging)
        if gated:
            needs_restart = True

        # Skills the manifest names: rebuild from the upstream set only.
        # Package-only skills are NOT touched here — the boot-time local
        # fetch owns their content (and mirrors the package).
        for name in sorted(manifest_dirs):
            if name in doomed_dirs:
                continue
            want = {
                rel.split('/', 1)[1]: content
                for rel, content in desired.items()
                if rel.startswith(f'{name}/')
            }
            have = {
                rel.split('/', 1)[1]: path
                for rel, path in existing.items()
                if rel.startswith(f'{name}/')
            }
            if have == {} and want == {}:
                continue
            if set(have) == set(want):
                identical = 0
                for rel, path in have.items():
                    if path.read_bytes() == want[rel]:
                        identical += 1
                if identical == len(want):
                    skipped += len(want)
                    continue
            staging = self._make_staging(f'{name}_')
            try:
                for rel, content in sorted(want.items()):
                    target = staging / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                changed = sorted(
                    set(have) ^ set(want)
                    | {
                        rel
                        for rel in have
                        if have[rel].read_bytes() != want.get(rel)
                    }
                )
                for rel in changed:
                    if self._is_gate_path(f'{name}/{rel}'):
                        needs_restart = True
                for rel, content in want.items():
                    old = have.get(rel)
                    if old is not None and old.read_bytes() == content:
                        skipped += 1
                    else:
                        copied += 1
                self._swap_in(staging, self._dest_dir / name)
                staging = None
            finally:
                if staging is not None:
                    _remove(staging)

        # Files at the manifest root (no skill dir): written in place.
        for rel, content in sorted(desired.items()):
            if '/' in rel:
                continue
            if self._install_file(self._dest_dir / rel, content):
                copied += 1
            else:
                skipped += 1

        for rel in doomed_files:
            path = existing[rel]
            _remove(path)
            if self._is_gate_path(rel):
                needs_restart = True

        if doomed_dirs:
            self._forget_synced_skills(doomed_dirs)
        return {
            'copied': copied,
            'skipped': skipped,
            'deleted': len(doomed_dirs) + len(doomed_files),
            'needs_restart': needs_restart,
            'refused': None,
            'failed': len(failed),
        }

    def _guard_mass_delete(
        self,
        *,
        doomed_dirs: list[str],
        doomed_files: list[str],
        owned_dirs: set[str],
        existing: dict[str, Path],
    ) -> bool:
        """Refuse a run that would delete most of the tree.

        A truncated or hostile manifest must not wipe skills. Half is
        a deliberately blunt line: no legitimate upstream edit removes
        most of what it ships, and the cost of a false refusal is one
        late sync — the cost of a missing guard is a wiped tree.
        """
        total_dirs = max(len(owned_dirs), 1)
        owned_file_count = sum(
            1
            for rel in existing
            if '/' in rel and rel.split('/', 1)[0] in owned_dirs
        )
        total_files = max(owned_file_count, 1)
        if len(doomed_dirs) * 2 > total_dirs:
            logger.warning(
                'refusing to delete %d of %d synced skills',
                len(doomed_dirs),
                total_dirs,
            )
            return True
        if len(doomed_files) * 2 > total_files:
            logger.warning(
                'refusing to delete %d of %d synced files',
                len(doomed_files),
                total_files,
            )
            return True
        return False

    # ── Skill dependency management ─────────────────────
