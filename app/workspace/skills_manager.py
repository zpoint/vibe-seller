"""Skill management for the workspace.

Split out of ``manager.py`` to keep each module under the 800-line
limit. ``SkillsMixin`` is mixed into ``WorkspaceManager``, so ``self``
is the full manager — these methods rely on ``self.root``,
``self.ensure_init``, ``self._auto_commit`` and
``self._parse_yaml_frontmatter``.

Covers the skills lockfile helpers, plain create/delete, and the
user-triggered save-skill upsert (``list_skills`` / ``save_skill``).
"""

from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile

logger = logging.getLogger(__name__)


class SkillsMixin:
    """Skill CRUD + save-skill upsert for WorkspaceManager."""

    @property
    def _lockfile_path(self):
        return self.root / '.claude' / 'skills' / 'skills.lock.json'

    @staticmethod
    def _read_synced_skills(skills_dir: Path) -> set[str]:
        """Read the set of synced (builtin) skill names."""
        meta_path = skills_dir / '.sync_meta.json'
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding='utf-8'))
                return set(data.get('synced_skills', []))
            except Exception:
                pass
        return set()

    def _read_lockfile(self) -> dict:
        """Read skills lockfile, return default if missing/corrupt."""
        default = {'version': 1, 'skills': {}}
        try:
            if self._lockfile_path.exists():
                data = json.loads(
                    self._lockfile_path.read_text(encoding='utf-8')
                )
                if not isinstance(data, dict):
                    return default
                # Merge with defaults for missing keys
                data.setdefault('version', 1)
                data.setdefault('skills', {})
                if not isinstance(data['skills'], dict):
                    data['skills'] = {}
                return data
        except (json.JSONDecodeError, OSError):
            logger.warning('Corrupt skills lockfile, using default')
        return default

    def _write_lockfile(self, data: dict) -> None:
        """Write skills lockfile atomically via temp + rename."""
        path = self._lockfile_path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, str(path))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    async def create_skill(
        self,
        name: str,
        description: str = '',
        content: str | None = None,
        origin_url: str = '',
    ) -> str:
        """Scaffold a new skill directory with SKILL.md.

        If content is provided, it replaces the default SKILL.md
        template.
        """
        if name.startswith('_'):
            raise ValueError(
                f'Skill names starting with _ are reserved: {name}'
            )
        slug = re.sub(r'[^a-z0-9-]', '-', name.lower()).strip('-')
        skill_dir = self.root / '.claude' / 'skills' / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / 'SKILL.md'
        if content is not None:
            skill_md.write_text(content, encoding='utf-8')
        else:
            default = f"""---
name: {name}
description: {description}
---

# {name}

{description}

## Instructions

<!-- Add skill instructions here -->
"""
            skill_md.write_text(default, encoding='utf-8')
        lockfile = self._read_lockfile()
        lockfile['skills'][slug] = {
            'source': 'url' if origin_url else 'local',
            'name': name,
            'origin_url': origin_url,
            'created_at': datetime.now(UTC).isoformat(),
            'updated_at': datetime.now(UTC).isoformat(),
        }
        self._write_lockfile(lockfile)
        await self._auto_commit(f'Create skill: {name}')
        return str(skill_dir.relative_to(self.root))

    async def delete_skill(self, slug: str) -> None:
        """Delete a user skill directory and lockfile entry."""
        # Validate slug: only lowercase alphanumeric and hyphens
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', slug):
            raise ValueError(f'Invalid skill slug: {slug}')
        if slug.startswith('_'):
            raise ValueError('Cannot delete built-in skills')
        skill_dir = self.root / '.claude' / 'skills' / slug
        if not skill_dir.is_dir():
            raise FileNotFoundError(f'Skill not found: {slug}')
        shutil.rmtree(skill_dir)
        lockfile = self._read_lockfile()
        lockfile['skills'].pop(slug, None)
        self._write_lockfile(lockfile)
        await self._auto_commit(f'Delete skill: {slug}')

    @staticmethod
    def _classify_skill(slug: str, synced: set[str], lockfile: dict) -> str:
        """Return 'builtin' | 'imported' | 'custom' for a skill slug.

        Mirrors get_structured: maintainer-synced slugs are built-in
        (read-only), lockfile source=='url' is imported, everything
        else is user-authored 'custom'.
        """
        if slug in synced:
            return 'builtin'
        if lockfile['skills'].get(slug, {}).get('source') == 'url':
            return 'imported'
        return 'custom'

    async def list_skills(self) -> list[dict]:
        """List skills with the fields the save-skill flow needs.

        Returns ``{slug, name, description, source, updatable}`` per
        skill. ``updatable`` is true only for user-editable skills
        (custom + imported); built-ins are read-only.
        """
        await self.ensure_init()
        skills_dir = self.root / '.claude' / 'skills'
        if not skills_dir.is_dir():
            return []
        lockfile = self._read_lockfile()
        synced = self._read_synced_skills(skills_dir)
        out: list[dict] = []
        for skill_path in sorted(skills_dir.iterdir()):
            if not skill_path.is_dir() or skill_path.name.startswith('.'):
                continue
            slug = skill_path.name
            source = self._classify_skill(slug, synced, lockfile)
            name, description = slug, ''
            skill_md = skill_path / 'SKILL.md'
            if skill_md.is_file():
                try:
                    fm = self._parse_yaml_frontmatter(
                        skill_md.read_text(encoding='utf-8')
                    )
                    name = fm.get('name') or slug
                    description = fm.get('description', '')
                except Exception:
                    pass
            out.append({
                'slug': slug,
                'name': name,
                'description': description,
                'source': source,
                'updatable': source in ('custom', 'imported'),
            })
        return out

    async def save_skill(
        self,
        slug: str,
        skill_md: str,
        files: dict[str, str] | None = None,
    ) -> dict:
        """Create or overwrite a USER-space skill; auto-commit.

        Hard-rejects built-in (maintainer-synced) slugs — they are
        read-only, so the save-skill flow can only ever create or
        extend user-space skills. Overwriting an existing custom or
        imported skill is how a workflow is *extended*: the caller
        passes the full merged SKILL.md.
        """
        await self.ensure_init()
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', slug or ''):
            raise ValueError(
                f'Invalid skill slug: {slug!r} '
                '(use lowercase letters, digits, hyphens)'
            )
        skills_dir = self.root / '.claude' / 'skills'
        synced = self._read_synced_skills(skills_dir)
        if slug in synced:
            raise ValueError(
                f'{slug!r} is a built-in skill (maintainer-owned, '
                'read-only). Create a new user-space skill with a '
                'different slug instead — it may reference the built-in.'
            )
        skill_dir = skills_dir / slug
        existed = skill_dir.is_dir()
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / 'SKILL.md').write_text(skill_md, encoding='utf-8')
        for rel, content in (files or {}).items():
            p = Path(rel)
            if (
                p.is_absolute()
                or not p.parts
                or any(part == '..' for part in p.parts)
                or p.name == 'SKILL.md'
            ):
                raise ValueError(f'Invalid skill file path: {rel!r}')
            dest = skill_dir / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding='utf-8')
        lockfile = self._read_lockfile()
        now = datetime.now(UTC).isoformat()
        fm = self._parse_yaml_frontmatter(skill_md)
        entry = lockfile['skills'].get(slug, {})
        entry.setdefault('source', 'local')
        entry.setdefault('origin_url', '')
        entry.setdefault('created_at', now)
        entry['name'] = fm.get('name') or entry.get('name') or slug
        entry['updated_at'] = now
        lockfile['skills'][slug] = entry
        self._write_lockfile(lockfile)
        await self._auto_commit(
            f'{"Update" if existed else "Create"} skill: {slug}'
        )
        return {
            'slug': slug,
            'path': str(skill_dir.relative_to(self.root)),
            'action': 'updated' if existed else 'created',
        }
