"""Skills sync: copies built-in skills to local workspace.

Architecture:
  PACKAGE (app/skills/)               LOCAL (~/.vibe-seller/)
    amazon-invoice/SKILL.md             .claude/skills/
    amazon-invoice/generate_invoice.py    amazon-invoice/  <- synced
    ...                                   user-skill/     <- user-created

Two sync tiers (mirrors knowledge_sync.py):
  1. Local package — importlib.resources, once at server boot
  2. Remote GitHub — MANIFEST.txt + changed files, driven by the
     central sync scheduler's ``skills`` job (app/sync/jobs.py)

WHEN the remote tier runs is not decided here: that is the scheduler's
``sync_state`` row (last_ok_at + cadence + jitter). This module only
knows how to ask upstream what it has (:meth:`SkillsSyncManager.
probe_remote`) and how to put it on disk without ever exposing a
half-written skill (:meth:`SkillsSyncManager.do_remote_sync`).

After sync, skill Python dependencies (requirements.txt) are
auto-installed into the shared workspace venv (~/.vibe-seller/.venv/).
Skills do NOT have their own .venv — agents use ``python`` from PATH.
"""

import asyncio
import hashlib
import importlib.resources
import json
import logging
from pathlib import Path
import shutil

import httpx

from app.config import AI_BOT_USER_ID, SKILLS_REPO_URL, SKILLS_SUBDIR
from app.database import async_session
from app.models.event import Event
from app.platform import venv_executable, venv_python
from app.plugins import registered_skill_sources
from app.sync import github as github_probe
from app.workspace.manager import VIBE_SELLER_DIR
from app.workspace.skill_mirror import (
    _COPY_IGNORE,
    _PACKAGE_ONLY_SUFFIXES,
    SkillMirrorMixin,
    _remove,
)

logger = logging.getLogger(__name__)


class SkillsSyncManager(SkillMirrorMixin):
    """Syncs built-in skills to ~/.vibe-seller/.claude/skills/.

    Skills are direct children of the skills/ directory so that
    Claude Code's automatic skill discovery can find them.
    """

    def __init__(self):
        self._dest_dir = VIBE_SELLER_DIR / '.claude' / 'skills'
        self._lock = asyncio.Lock()
        # Strong ref to the deferred boot-time dep install (see fetch).
        self._deps_task: asyncio.Task | None = None

    @staticmethod
    def _log_deps_result(task: asyncio.Task) -> None:
        """Surface failures from the deferred install — background
        task exceptions are otherwise silently dropped."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error('Deferred skill dep install failed: %s', exc)

    async def wait_deps_ready(self) -> None:
        """Join point for the deferred boot-time dep install.

        Task launches await this so skill scripts can rely on their
        requirements being installed; SERVING never waits (that is
        the whole point of defer_deps). Install failures are logged
        by the done-callback and are non-fatal here — matching the
        pre-existing per-skill best-effort semantics.
        """
        task = self._deps_task
        if task is not None and not task.done():
            try:
                await asyncio.shield(task)
            except Exception:
                pass

    # ── Staging (atomic tree updates) ───────────────────

    def _get_local_source(self) -> Path | None:
        """Find bundled skills tree via importlib.resources.

        The subdir (``skills`` legacy vs ``skills_v2``) is pinned by
        :data:`app.config.SKILLS_SUBDIR` — the one place that decides which
        skill tree this release line ships and syncs. See
        docs/browser-use-0.13-migration.md.
        """
        try:
            ref = importlib.resources.files('app') / SKILLS_SUBDIR
            p = Path(str(ref))
            return p if p.is_dir() else None
        except Exception:
            return None

    @property
    def source_dir(self) -> Path | None:
        return self._get_local_source()

    def _get_source_dirs(self) -> list[Path]:
        """Skill source dirs to sync: core's ``app/skills`` + plugins'.

        ``app/skills`` is core's own bundled source (public OSS skills
        stay there — it is NOT a plugin contribution), so it stays the
        primary/first source. Plugins (e.g. external customer
        wheels) register ADDITIONAL skill dirs via :mod:`app.plugins`,
        appended here. De-duped on resolved path, order preserved, so a
        plugin can't double-sync an already-listed skill dir.
        """

        sources: list[Path] = []
        local = self._get_local_source()
        if local:
            sources.append(local)
        sources.extend(registered_skill_sources())
        seen: set[Path] = set()
        ordered: list[Path] = []
        for s in sources:
            rp = s.resolve()
            if s.is_dir() and rp not in seen:
                seen.add(rp)
                ordered.append(s)
        return ordered

    async def fetch(self, *, defer_deps: bool = False) -> dict:
        """Sync built-in + plugin skill dirs to the workspace.

        Each skill directory is replaced atomically: build it in the
        staging root beside the tree, then rename it into place. The
        staging root is never inside ``.claude/`` — that whole tree is
        copied into every task workspace. User-created skills (no
        matching source dir) are left untouched.

        Serialized with an asyncio.Lock so concurrent task launches
        don't race.

        ``defer_deps=True`` (the boot path) runs the per-skill pip
        installs as a background task instead of blocking the caller
        — cold installs can take tens of seconds and must never keep
        the health endpoint down.
        """
        srcs = self._get_source_dirs()
        if not srcs:
            return {
                'synced': False,
                'reason': 'No skills/ in installed package',
            }

        async with self._lock:
            return await self._fetch_locked(srcs, defer_deps=defer_deps)

    async def _fetch_locked(
        self, srcs: list[Path], *, defer_deps: bool = False
    ) -> dict:
        """Inner fetch, called under lock."""
        self._dest_dir.mkdir(parents=True, exist_ok=True)
        replaced = 0
        skipped = 0
        synced_names: list[str] = []
        # First source wins on a skill-NAME collision: core's app/skills
        # (always srcs[0]) must not be silently shadowed by a same-named
        # skill from a later plugin source. Dedup on name, not path.
        seen_names: set[str] = set()

        for src in srcs:
            for src_skill in sorted(src.iterdir()):
                if not src_skill.is_dir():
                    continue
                if src_skill.name.startswith(('.', '_')):
                    continue
                if src_skill.name in seen_names:
                    logger.warning(
                        'Skill %r from %s shadowed by an earlier source; '
                        'skipping',
                        src_skill.name,
                        src,
                    )
                    continue
                seen_names.add(src_skill.name)

                synced_names.append(src_skill.name)
                dest_skill = self._dest_dir / src_skill.name

                # Quick content check: skip if unchanged
                if (
                    dest_skill.is_dir()
                    and not dest_skill.is_symlink()
                    and self._skill_unchanged(src_skill, dest_skill)
                ):
                    skipped += 1
                    continue

                # Atomic replace: build beside the tree, rename in.
                tmp = self._make_staging(f'{src_skill.name}_')
                try:
                    # mkdtemp creates the dir; copy into it with
                    # dirs_exist_ok so it can populate the existing
                    # empty temp dir.
                    shutil.copytree(
                        src_skill,
                        tmp,
                        ignore=_COPY_IGNORE,
                        dirs_exist_ok=True,
                    )
                    self._swap_in(tmp, dest_skill)
                except Exception:
                    _remove(tmp)
                    raise
                replaced += 1

        # In-place-upgrade cleanup: the browser skill was renamed
        # browser-use -> browser-harness in the browser-use 0.13
        # migration (docs/browser-use-0.13-migration.md). On a client
        # upgraded in place, the stale 0.12 ``browser-use`` skill lingers
        # in the workspace next to the new ``browser-harness`` and could
        # be auto-discovered / loaded instead — feeding the agent 0.12
        # subcommand syntax against a 0.13 binary. Remove it whenever the
        # current source ships ``browser-harness`` and not ``browser-use``.
        # Scoped to that exact rename, so it can never touch a
        # user-created skill or a legitimately-shipped ``browser-use``.
        if 'browser-harness' in seen_names and 'browser-use' not in seen_names:
            stale = self._dest_dir / 'browser-use'
            if stale.is_dir() and not stale.is_symlink():
                shutil.rmtree(stale, ignore_errors=True)
                replaced += 1
                logger.info(
                    'Removed superseded browser-use skill '
                    '(renamed to browser-harness in the 0.13 migration)'
                )

        # Mirror the PACKAGE's set too: a release that drops a skill
        # must drop it locally, without waiting for the remote job to
        # notice. Scoped to skills the previous boot saw the package
        # ship — a remote-only skill is never in that set and is left
        # for the remote job to own.
        self._reap_dropped_package_skills(synced_names)

        # Track synced skills from source dirs (not dest contents)
        self._update_synced_skills(synced_names)

        # Auto-install skill Python deps into shared workspace venv.
        # On the boot path this is deferred to a background task so
        # serving (incl. /api/health) starts immediately; task
        # launches join it via wait_deps_ready().
        if defer_deps:
            self._deps_task = asyncio.create_task(self._install_skill_deps())
            self._deps_task.add_done_callback(self._log_deps_result)
        else:
            await self._install_skill_deps()

        logger.info(
            'Skills sync (local): %d replaced, %d unchanged',
            replaced,
            skipped,
        )
        return {
            'synced': True,
            'replaced': replaced,
            'skipped': skipped,
        }

    @staticmethod
    def _skill_unchanged(src_dir: Path, dest_dir: Path) -> bool:
        """Check if all source files exist in dest with same content."""
        for src_file in src_dir.rglob('*'):
            if not src_file.is_file():
                continue
            if src_file.name.startswith('.'):
                continue
            if src_file.name in _PACKAGE_ONLY_SUFFIXES:
                continue
            if '__pycache__' in src_file.parts:
                continue
            dest_file = dest_dir / src_file.relative_to(src_dir)
            if not dest_file.exists():
                return False
            try:
                if src_file.read_bytes() != dest_file.read_bytes():
                    return False
            except Exception:
                return False
        # Also check dest doesn't have extra files or stale dirs
        for dest_path in dest_dir.rglob('*'):
            rel_parts = dest_path.relative_to(dest_dir).parts
            # Treat any .venv or __pycache__ path component as stale
            if '__pycache__' in rel_parts or '.venv' in rel_parts:
                return False
            if not dest_path.is_file():
                continue
            if dest_path.name.startswith('.'):
                continue
            src_file = src_dir / dest_path.relative_to(dest_dir)
            if not src_file.exists():
                return False
        return True

    # ── Inventory (.sync_meta.json) ─────────────────────

    # NOTE: this file holds INVENTORY only — which skill dirs this
    # manager put on disk (``synced_skills``), whether the optional gws
    # bundle is installed (``gws_installed``) and which requirements.txt
    # hashes are already pip-installed (``installed_deps``). Schedule
    # state (last sync, upstream commit, ...) lives in the ``sync_state``
    # table; see app/sync/state.py.

    @property
    def _sync_meta_path(self) -> Path:
        # Derive from ``self._dest_dir`` so test fixtures that
        # override ``_dest_dir`` to a tmp dir also redirect the meta
        # file. Using a module-level constant instead would leak
        # inventory between test runs on hosts where ``~/.vibe-seller``
        # persists (self-hosted CI).
        return self._dest_dir / '.sync_meta.json'

    def _read_sync_meta(self) -> dict:
        """Read .sync_meta.json."""
        path = self._sync_meta_path
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return {}

    def _write_sync_meta(self, meta: dict) -> None:
        """Write .sync_meta.json."""
        path = self._sync_meta_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, indent=2))

    # ── Google Workspace bundle flag ────────────────────

    def is_gws_installed(self) -> bool:
        """Return True if the gws umbrella has been installed."""
        return bool(self._read_sync_meta().get('gws_installed', False))

    def mark_gws_installed(self, installed: bool) -> None:
        """Record whether the gws umbrella is installed."""
        meta = self._read_sync_meta()
        if installed:
            meta['gws_installed'] = True
        else:
            meta.pop('gws_installed', None)
        self._write_sync_meta(meta)

    def get_synced_skills(self) -> set[str]:
        """Return set of skill names synced by this manager."""
        return set(self._read_sync_meta().get('synced_skills', []))

    def _reap_dropped_package_skills(self, shipped: list[str]) -> None:
        """Delete package skills the installed package no longer ships.

        Same mirror rule as the remote tier, same guard: a run that
        would delete most of the tree is refused and nothing changes.
        """
        meta = self._read_sync_meta()
        previous = set(meta.get('package_skills') or [])
        now = set(shipped)
        dropped = sorted(
            name
            for name in previous - now
            if name in self.get_synced_skills()
            and (self._dest_dir / name).is_dir()
        )
        if dropped and len(dropped) * 2 > max(len(previous), 1):
            logger.warning(
                'refusing to drop %d of %d package skills',
                len(dropped),
                len(previous),
            )
            return
        for name in dropped:
            _remove(self._dest_dir / name)
            logger.info('Dropped package skill %s (no longer shipped)', name)
        meta = self._read_sync_meta()
        meta['package_skills'] = sorted(now)
        self._write_sync_meta(meta)

    def _forget_synced_skills(self, names: list[str]) -> None:
        """Drop skills sync removed from the inventory."""
        meta = self._read_sync_meta()
        remaining = set(meta.get('synced_skills') or []) - set(names)
        meta['synced_skills'] = sorted(remaining)
        self._write_sync_meta(meta)

    def _update_synced_skills(self, names: list[str]) -> None:
        """Merge skill names into .sync_meta.json."""
        meta = self._read_sync_meta()
        existing = set(meta.get('synced_skills', []))
        existing.update(names)
        meta['synced_skills'] = sorted(existing)
        self._write_sync_meta(meta)

    # ── Remote sync ─────────────────────────────────────

    async def probe_remote(
        self, client: httpx.AsyncClient, etag: str | None = None
    ) -> dict:
        """Ask GitHub for the latest commit of this skill tree.

        See :func:`app.sync.github.probe_commit`. ``etag`` is the
        validator stored from the previous probe; a 304 answer is the
        common case and costs nothing against the rate limit.
        """
        return await github_probe.probe_commit(
            client, f'app/{SKILLS_SUBDIR}', etag=etag
        )

    async def do_remote_sync(self, client: httpx.AsyncClient) -> dict:
        """Download files from remote URL using MANIFEST.txt.

        Remote sync NEVER overwrites a file that exists in the local
        package source. Rationale: the installed package version is
        the authoritative content for the lifetime of that version —
        ``pip install vibe-seller==X.Y.Z`` is the user's signed-up
        contract. Remote sync exists to add NEW skills shipped after
        the user's release, not to silently mutate skills the user
        already has.

        Without this guard, a dev-checkout or in-flight CI run gets
        its local skills clobbered by the public-main version, which
        in CI manifested as the skill-prereq hook silently never
        seeing the new ``requires:`` frontmatter field.

        Every network read happens before the first disk write (see
        :meth:`_apply_downloads`) so the tree is never left
        half-updated in front of a task launch's ``copytree``.
        """
        base_url = SKILLS_REPO_URL.rstrip('/')
        local_source = self._get_local_source()
        try:
            # 1. Fetch MANIFEST.txt
            resp = await client.get(
                f'{base_url}/MANIFEST.txt',
                timeout=15,
            )
            if resp.status_code != 200:
                err = (
                    'Failed to fetch skills MANIFEST.txt: '
                    f'HTTP {resp.status_code}'
                )
                logger.warning(err)
                return {'status': 'failed', 'error': err}

            files = [
                line.strip()
                for line in resp.text.splitlines()
                if line.strip() and not line.startswith('#')
            ]
            if not files:
                # An empty (or unparseable) manifest is not "upstream is
                # empty" — it is a broken answer, and treating it as the
                # former would delete the tree.
                err = 'skills MANIFEST.txt is empty; refusing to delete'
                logger.warning(err)
                return {'status': 'failed', 'error': err, 'deleted': 0}

            # 2. Download each file into memory. All awaits end here.
            downloads: dict[str, bytes] = {}
            listed = set(files)
            failed: set[str] = set()
            local_skips = 0
            for rel_path in files:
                # Path traversal validation (resolve follows symlinks)
                resolved = (self._dest_dir / rel_path).resolve()
                if not str(resolved).startswith(str(self._dest_dir.resolve())):
                    logger.warning(
                        'Path traversal in MANIFEST: %s',
                        rel_path,
                    )
                    continue

                # Local-package precedence: if this file exists in
                # the installed package, the package version is
                # authoritative — do not let remote overwrite it.
                if local_source and (local_source / rel_path).is_file():
                    local_skips += 1
                    continue

                url = f'{base_url}/{rel_path}'
                try:
                    file_resp = await client.get(url, timeout=15)
                except Exception as e:
                    logger.warning('Error downloading %s: %s', rel_path, e)
                    failed.add(rel_path)
                    continue
                if file_resp.status_code != 200:
                    logger.warning(
                        'Failed to fetch %s: HTTP %d',
                        rel_path,
                        file_resp.status_code,
                    )
                    failed.add(rel_path)
                    continue
                downloads[rel_path] = file_resp.content

            # 3. Put it on disk, atomically.
            applied = self._apply_downloads(downloads, listed, failed)
            if applied.get('failed'):
                # A listed file we could not fetch is not an upstream
                # deletion: keep the tree, keep last_ok_at where it is,
                # and retry next cadence.
                err = f'{applied["failed"]} files failed to download'
                logger.warning('Skills sync (remote): %s', err)
                return {
                    'status': 'failed',
                    'error': err,
                    'copied': applied['copied'],
                    'skipped': applied['skipped'],
                    'deleted': 0,
                    'needs_restart': applied['needs_restart'],
                }
            if applied.get('refused'):
                err = str(applied['refused'])
                logger.warning('Skills sync (remote): %s', err)
                return {
                    'status': 'failed',
                    'error': err,
                    'copied': 0,
                    'skipped': 0,
                    'deleted': 0,
                    'needs_restart': False,
                }

            # Track synced skill dirs from manifest (not dest)
            manifest_skill_dirs: set[str] = set()
            for rel_path in files:
                parts = Path(rel_path).parts
                if parts:
                    manifest_skill_dirs.add(parts[0])
            self._update_synced_skills(sorted(manifest_skill_dirs))

            # Post-sync: install deps into shared venv
            await self._install_skill_deps()

            applied['skipped'] += local_skips
            logger.info(
                'Skills sync (remote): %d copied, %d unchanged, %d deleted',
                applied['copied'],
                applied['skipped'],
                applied['deleted'],
            )
            if applied['copied']:
                await self._log_sync_event(
                    'success',
                    f'Synced {applied["copied"]} skill files from remote',
                )
            return {
                'status': 'success',
                'copied': applied['copied'],
                'skipped': applied['skipped'],
                'deleted': applied['deleted'],
                'needs_restart': applied['needs_restart'],
            }

        except Exception as e:
            err = f'Remote skills sync error: {e}'
            logger.warning(err)
            await self._log_sync_event('failed', err)
            return {'status': 'failed', 'error': err}

    async def _install_skill_deps(self) -> None:
        """Install skill requirements.txt into shared venv.

        Uses ``uv pip install`` which applies a file-based lock
        on the target venv, so concurrent calls are safe.
        """
        venv = VIBE_SELLER_DIR / '.venv'
        py = venv_python(venv)
        if not py.exists():
            return

        meta = self._read_sync_meta()
        installed = meta.get('installed_deps', {})
        changed = False

        for skill_dir in self._dest_dir.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                continue
            req = skill_dir / 'requirements.txt'
            if not req.exists():
                continue

            # Skip if requirements.txt content hasn't changed
            try:
                content_hash = hashlib.md5(req.read_bytes()).hexdigest()
            except Exception:
                content_hash = ''
            if installed.get(skill_dir.name) == content_hash:
                continue

            logger.info(
                'Installing deps for skill %s into shared venv',
                skill_dir.name,
            )
            try:
                uv_bin = venv_executable(venv, 'uv')
                if uv_bin.exists():
                    cmd = [
                        str(uv_bin),
                        'pip',
                        'install',
                        '-r',
                        str(req),
                        '--python',
                        str(py),
                    ]
                else:
                    cmd = [
                        str(py),
                        '-m',
                        'pip',
                        'install',
                        '-r',
                        str(req),
                    ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    logger.warning(
                        'Failed to install deps for %s: %s',
                        skill_dir.name,
                        stderr.decode()[:200] if stderr else '',
                    )
                    continue
                installed[skill_dir.name] = content_hash
                changed = True
            except Exception as e:
                logger.warning(
                    'Error installing deps for %s: %s',
                    skill_dir.name,
                    e,
                )

        if changed:
            meta['installed_deps'] = installed
            self._write_sync_meta(meta)

    # ── System event logging ────────────────────────────

    async def _log_sync_event(
        self,
        status: str,
        details: str,
    ) -> None:
        """Log a sync result as a system Event."""
        try:
            async with async_session() as db:
                event = Event(
                    title=f'Skills sync: {status}',
                    description=details,
                    status=('resolved' if status == 'success' else 'open'),
                    created_by=AI_BOT_USER_ID,
                    platform='system',
                )
                db.add(event)
                await db.commit()
        except Exception as e:
            logger.warning('Failed to log sync event: %s', e)


# Singleton
skills_sync = SkillsSyncManager()
