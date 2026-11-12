"""Skill management for the workspace.

Split out of ``manager.py`` to keep each module under the 800-line
limit. ``SkillsMixin`` is mixed into ``WorkspaceManager``, so ``self``
is the full manager — these methods rely on ``self.root``,
``self.ensure_init`` and ``self._auto_commit``.

Covers the skills lockfile helpers, plain create/delete, and the
user-triggered save-skill upsert (``list_skills`` / ``save_skill``).

The write contract — one place, so every caller (web UI, REST, the
external MCP ``manage_skill`` tool, the in-task save-skill tool) is
held to the same rules:

* a skill is SKILL.md plus any tree of supporting files, and the
  whole tree is written from ONE call (``save_skill``) or not at all
  (every path and size is checked before the first byte is written);
* text files ride as ``str``; anything that is not valid UTF-8 rides
  as base64 in ``files_bin`` and is stored byte-identical
  (``write_bytes`` — no sanitizing, no re-encoding);
* file paths are safe relative skill paths: no ``..``, no absolute
  paths, no backslashes, never ``SKILL.md`` itself (matched
  case-insensitively — the workspace may live on a
  case-insensitive filesystem where ``skill.md`` IS ``SKILL.md``),
  and no write may follow a symlink out of the skill directory;
* text is stored as UTF-8 (sanitized); ``files_bin`` is stored
  BYTE-IDENTICAL (``write_bytes`` — no sanitizing, no re-encoding);
* everything is bounded: :data:`MAX_SKILL_FILES` files,
  :data:`MAX_SKILL_FILE_BYTES` each, :data:`MAX_SKILL_TOTAL_BYTES`
  per call, before base64 inflation is materialised;
* ``executables`` names the files installed with the executable bit;
* ``replace=True`` (the import flow) installs the tree WHOLESALE —
  it is staged in a temp dir and swapped in, so the previous files
  are gone and a mid-write failure leaves the old skill untouched.
  The default (the UI / save-skill flow) MERGES: it writes the files
  it is given and leaves the rest of the skill alone. Replacing an
  EXISTING user skill needs ``overwrite=True`` — never a silent
  destruction of someone's hand-made skill.
"""

import asyncio
import base64
import binascii
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile

from app.text_utils import sanitize_text
from app.utils.json_file import update_json_file
from app.workspace import skills_tree

logger = logging.getLogger(__name__)

# Size / count ceilings for one save-skill call. Bounded here, not at
# the transport: the REST route, the MCP tools and the in-task tool
# all land in ``save_skill``, and the endpoint body cap alone would
# still let one call write an unbounded tree file by file.
MAX_SKILL_FILES = 200
MAX_SKILL_FILE_BYTES = 5 * 1024 * 1024
MAX_SKILL_TOTAL_BYTES = 25 * 1024 * 1024


def parse_yaml_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter from a markdown file (--- delimited).

    Deliberately line-oriented (not a YAML parse): all the callers
    want is ``name`` / ``description``, and this cannot choke on the
    richer frontmatter other clients write (codex's ``metadata:``
    block, say) — an imported SKILL.md is stored as written whatever
    its frontmatter holds.
    """
    if not text.startswith('---'):
        return {}
    end = text.find('---', 3)
    if end == -1:
        return {}
    frontmatter = text[3:end].strip()
    result = {}
    for line in frontmatter.split('\n'):
        if ':' in line:
            key, _, value = line.partition(':')
            result[key.strip()] = value.strip()
    return result


def skill_slug(name: str) -> str:
    """The slug a skill name maps to (``create_skill``'s rule)."""
    return re.sub(r'[^a-z0-9-]', '-', name.lower()).strip('-')


def derive_skill_slug(skill_md: str) -> str:
    """The slug a SKILL.md imports under: its frontmatter ``name``.

    This is the mapping a caller cannot guess (frontmatter names may
    hold spaces and capitals; the slug may not) — one rule, here, so
    an import never depends on the caller re-deriving it. Empty when
    the frontmatter carries no name.
    """
    return skill_slug(parse_yaml_frontmatter(skill_md).get('name') or '')


def _check_skill_file_path(rel: str) -> Path:
    """Validate one relative skill-file path; return its ``Path``.

    Safe-relative only: no absolute paths, no ``..``, no backslashes,
    nothing empty, and never ``SKILL.md`` (case-insensitively).
    """
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError(f'Invalid skill file path: {rel!r}')
    p = Path(rel)
    if (
        p.is_absolute()
        or not p.parts
        or rel.startswith('/')
        or '\\' in rel
        or any(part == '..' for part in p.parts)
        or p.name.lower() == 'skill.md'
    ):
        raise ValueError(f'Invalid skill file path: {rel!r}')
    return p


def _refuse_symlink_escape(skill_dir: Path, dest: Path, rel: str) -> Path:
    """Refuse a write that would follow a symlink out of the skill.

    Custom skill directories may themselves be symlinks (a skill kept
    in its own repo) — that is fine: ``skill_dir`` is resolved as the
    base. What must NOT happen is a write through a symlinked
    COMPONENT (a subdir link) or onto a symlinked FILE that points
    outside the skill — ``write_bytes`` would follow it and clobber
    an unrelated file. So: no existing symlink between the skill dir
    and the destination, and the resolved destination must stay
    inside the resolved skill dir.
    """
    base = skill_dir.resolve()
    current = skill_dir
    for part in dest.relative_to(skill_dir).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f'Skill file path crosses a symlink: {rel!r}')
    if not dest.resolve().is_relative_to(base):
        raise ValueError(f'Skill file path escapes the skill: {rel!r}')
    return dest


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
        slug = skill_slug(name)
        skill_dir = self.root / '.claude' / 'skills' / slug
        async with skills_tree.hold():
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_md = skill_dir / 'SKILL.md'
            if content is not None:
                await asyncio.to_thread(
                    skill_md.write_text,
                    sanitize_text(content),
                    encoding='utf-8',
                )
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
                await asyncio.to_thread(
                    skill_md.write_text, default, encoding='utf-8'
                )
            lockfile = self._read_lockfile()
            lockfile['skills'][slug] = {
                'source': 'url' if origin_url else 'local',
                'name': name,
                'origin_url': origin_url,
                'created_at': datetime.now(UTC).isoformat(),
                'updated_at': datetime.now(UTC).isoformat(),
            }
            self._write_lockfile(lockfile)
        # Git runs outside the tree lock: it is slow, and every task
        # launch waits on that lock for the .claude copy.
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
        async with skills_tree.hold():
            await asyncio.to_thread(shutil.rmtree, skill_dir)
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
                    fm = parse_yaml_frontmatter(
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

    @staticmethod
    def _skill_file_payloads(
        skill_md: str,
        files: dict[str, str] | None,
        files_bin: dict[str, str] | None,
    ) -> dict:
        """Validate + decode every extra file BEFORE anything is written.

        Returns ``{rel path: content}`` where content is ``str`` for
        text and ``bytes`` for base64. Raises ``ValueError`` on the
        first bad path, bad base64, or over-limit file — the caller
        then writes nothing.
        """
        if files is not None and not isinstance(files, dict):
            raise ValueError('files must be an object path -> content')
        if files_bin is not None and not isinstance(files_bin, dict):
            raise ValueError('files_bin must be an object path -> base64')
        text_files = files or {}
        bin_files = files_bin or {}
        if len(text_files) + len(bin_files) > MAX_SKILL_FILES:
            raise ValueError(
                f'A skill may carry at most {MAX_SKILL_FILES} files'
            )
        if set(text_files) & set(bin_files):
            raise ValueError(
                'A skill file is text or base64, not both: '
                f'{sorted(set(text_files) & set(bin_files))}'
            )
        total = len(sanitize_text(skill_md).encode('utf-8'))
        payloads: dict = {}
        for rel, content in list(text_files.items()) + list(bin_files.items()):
            _check_skill_file_path(rel)
            if isinstance(content, str) and rel in bin_files:
                try:
                    content = base64.b64decode(content, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError(
                        f'Skill file {rel!r} is not valid base64'
                    ) from exc
            if not isinstance(content, str | bytes):
                raise ValueError(
                    f'Skill file {rel!r} content must be text or base64'
                )
            size = (
                len(content)
                if isinstance(content, bytes)
                else len(sanitize_text(content).encode('utf-8'))
            )
            if size > MAX_SKILL_FILE_BYTES:
                raise ValueError(
                    f'Skill file {rel!r} is {size} bytes — the per-file '
                    f'limit is {MAX_SKILL_FILE_BYTES}'
                )
            total += size
            if total > MAX_SKILL_TOTAL_BYTES:
                raise ValueError(
                    'This skill exceeds the total size limit of '
                    f'{MAX_SKILL_TOTAL_BYTES} bytes'
                )
            payloads[rel] = content
        return payloads

    @staticmethod
    def _skill_executables(
        executables: list[str] | None, payloads: dict
    ) -> set:
        """Validate the executable-bit list against the written files."""
        if executables is None:
            return set()
        if not isinstance(executables, list):
            raise ValueError('executables must be a list of skill file paths')
        exec_set = set()
        for rel in executables:
            _check_skill_file_path(rel)
            if rel not in payloads:
                raise ValueError(
                    f'executables names {rel!r}, which is not one of '
                    'the files being written'
                )
            exec_set.add(rel)
        return exec_set

    async def save_skill(
        self,
        slug: str,
        skill_md: str,
        files: dict[str, str] | None = None,
        files_bin: dict[str, str] | None = None,
        executables: list[str] | None = None,
        origin_url: str = '',
        replace: bool = False,
        overwrite: bool = False,
    ) -> dict:
        """Create or overwrite a USER-space skill; auto-commit.

        The whole skill — SKILL.md plus ``files`` (text, stored as
        UTF-8) and ``files_bin`` (base64, stored byte-identical) —
        lands in ONE call: every path, size and limit is checked
        before the first byte is written, so a refused call leaves
        nothing behind. ``executables`` names written files that get
        the executable bit.

        Two write modes, deliberately different:

        * ``replace=False`` (the save-skill / UI flow) **merges**: the
          files given are written, the skill's other files stay.
        * ``replace=True`` (the import flow) **replaces**: the tree is
          staged in a temp dir and swapped in wholesale, so files the
          new import does not carry are GONE and a mid-write failure
          leaves the previous skill untouched.

        A replace over an EXISTING user skill is a destructive act on
        someone's work, so it is refused unless ``overwrite=True`` —
        one deliberate second call after the user has confirmed.
        ``origin_url`` records provenance in the lockfile (the skill
        then lists as ``imported``). Hard-rejects built-in
        (maintainer-synced) slugs — they are read-only.
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
        # ONE thread hop for the whole filesystem operation, inside
        # the skills-tree lock — the shape app/ads_skill.py uses for
        # its bundle install: a sync helper does every bit of disk
        # work (validation that touches disk, the staged swap or the
        # merge writes, the lockfile update) and the async wrapper
        # only awaits it. The install rules live in ONE place,
        # ``save_skill_blocking`` — the archive import hosts the same
        # core in its own hop.
        async with skills_tree.hold():
            result, existed = await asyncio.to_thread(
                self.save_skill_blocking,
                slug,
                skill_md,
                files,
                files_bin,
                executables,
                origin_url,
                replace,
                overwrite,
            )
        await self._auto_commit(
            f'{"Update" if existed else "Create"} skill: {slug}'
        )
        return result

    def save_skill_blocking(
        self,
        slug: str,
        skill_md: str,
        files: dict | None = None,
        files_bin: dict | None = None,
        executables: list | None = None,
        origin_url: str = '',
        replace: bool = False,
        overwrite: bool = False,
    ) -> tuple[dict, bool]:
        """The synchronous install core of :meth:`save_skill`.

        Every rule of the write contract lives here — the slug shape,
        built-in refusal, the overwrite gate, payload validation, the
        staged swap (``replace``) or the merge writes, and the
        lockfile entry. Hosted in a thread (never on the event loop)
        by the async ``save_skill`` and by the archive import, so both
        sources share one install path — and the checks apply to BOTH:
        direct callers of this core (the archive import) skip the
        async wrapper's pre-validation, so the slug regex lives here
        where a ``..`` or ``/`` could otherwise walk out of the skills
        directory. Returns ``(result, existed)``.
        """
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
        existed = skill_dir.is_symlink() or skill_dir.exists()
        if replace and existed and not overwrite:
            source = self._classify_skill(slug, synced, self._read_lockfile())
            raise ValueError(
                f'a skill named {slug} already exists (source={source}); '
                'confirm with the user, then re-call with overwrite=true '
                'or choose another slug'
            )
        payloads = self._skill_file_payloads(skill_md, files, files_bin)
        exec_set = self._skill_executables(executables, payloads)
        if replace:
            self._install_skill_tree(skill_dir, skill_md, payloads, exec_set)
        else:
            skill_dir.mkdir(parents=True, exist_ok=True)
            self._write_skill_tree(
                skill_dir, skill_md, payloads, exec_set, guard=True
            )
        now = datetime.now(UTC).isoformat()
        fm = parse_yaml_frontmatter(skill_md)

        def _merge_entry(data: dict) -> None:
            data.setdefault('version', 1)
            skills = data.setdefault('skills', {})
            entry = skills.get(slug, {})
            entry.setdefault('source', 'local')
            entry.setdefault('origin_url', '')
            entry.setdefault('created_at', now)
            if origin_url:
                entry['origin_url'] = origin_url
                entry['source'] = 'url'
            entry['name'] = fm.get('name') or entry.get('name') or slug
            entry['updated_at'] = now
            skills[slug] = entry

        update_json_file(self._lockfile_path, _merge_entry)
        result = {
            'slug': slug,
            'path': str(skill_dir.relative_to(self.root)),
            'action': 'updated' if existed else 'created',
        }
        return result, existed

    @staticmethod
    def _write_skill_tree(
        root: Path,
        skill_md: str,
        payloads: dict,
        exec_set: set,
        guard: bool = False,
    ) -> None:
        """Write SKILL.md + every file under *root*.

        Text is sanitized at the encode (``app.text_utils``);
        ``files_bin`` entries are BYTES and are written untouched — a
        re-encode through text would corrupt them. With ``guard``
        (the in-place write path) every destination is checked
        against symlink escapes first.
        """
        if guard:
            md_dest = _refuse_symlink_escape(
                root, root / 'SKILL.md', 'SKILL.md'
            )
        else:
            md_dest = root / 'SKILL.md'
        md_dest.write_text(sanitize_text(skill_md), encoding='utf-8')
        for rel, content in payloads.items():
            dest = root / _check_skill_file_path(rel)
            if guard:
                dest = _refuse_symlink_escape(root, dest, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                dest.write_bytes(content)
            else:
                dest.write_text(sanitize_text(content), encoding='utf-8')
            if rel in exec_set:
                dest.chmod(0o755)

    def _install_skill_tree(
        self, skill_dir: Path, skill_md: str, payloads: dict, exec_set: set
    ) -> None:
        """Replace semantics: stage the whole tree, then swap it in.

        The staging dir sits next to the skill (same filesystem, so
        the renames are cheap and atomic) and starts with ``.`` so no
        skill listing can see it. The old tree (or symlink) is
        renamed aside, the new one renamed in, and the old one is
        removed only after the swap landed — a failure anywhere
        before that leaves the previous skill exactly as it was.
        """
        skills_dir = skill_dir.parent
        staging = Path(
            tempfile.mkdtemp(
                dir=str(skills_dir), prefix=f'.{skill_dir.name}-import-'
            )
        )
        # mkdtemp creates 0700 and the dir is renamed into place as
        # the skill dir — match the 0755 every other skill dir gets
        # from mkdir + umask, or the imported tree is unreadable to
        # anything but the server's user.
        staging.chmod(0o755)
        old: Path | None = None
        try:
            self._write_skill_tree(staging, skill_md, payloads, exec_set)
            if skill_dir.is_symlink() or skill_dir.exists():
                old = staging.parent / f'{staging.name}-old'
                skill_dir.rename(old)
            try:
                staging.rename(skill_dir)
            except OSError:
                if old is not None:
                    old.rename(skill_dir)  # roll back
                raise
            if old is not None:
                if old.is_symlink():
                    old.unlink()  # drop the link, not its target
                else:
                    shutil.rmtree(old)
        finally:
            if staging.exists() and not staging.is_symlink():
                shutil.rmtree(staging, ignore_errors=True)
