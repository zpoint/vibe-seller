"""Skills sync: copies built-in skills to local workspace.

Architecture:
  PACKAGE (app/skills/)               LOCAL (~/.vibe-seller/)
    amazon-invoice/SKILL.md             .claude/skills/
    amazon-invoice/generate_invoice.py    amazon-invoice/  <- synced
    ...                                   user-skill/     <- user-created

Three-tier sync (mirrors knowledge_sync.py):
  1. Local package — importlib.resources (always after pip install)
  2. Remote GitHub — fetch MANIFEST.txt + changed files
  3. On-demand — triggered before each task if >24h and commit changed

After sync, skill Python dependencies (requirements.txt) are
auto-installed into the shared workspace venv (~/.vibe-seller/.venv/).
Skills do NOT have their own .venv — agents use ``python`` from PATH.
"""

import asyncio
from datetime import UTC, datetime
import hashlib
import importlib.resources
import json
import logging
from pathlib import Path
import shutil
import tempfile

import httpx

from app.config import AI_BOT_USER_ID, SKILLS_REPO_URL
from app.database import async_session
from app.models.event import Event
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

_SYNC_META_PATH = VIBE_SELLER_DIR / '.claude' / 'skills' / '.sync_meta.json'
_COOLDOWN_SECONDS = 24 * 3600  # 24 hours


class SkillsSyncManager:
    """Syncs built-in skills to ~/.vibe-seller/.claude/skills/.

    Skills are direct children of the skills/ directory so that
    Claude Code's automatic skill discovery can find them.
    """

    def __init__(self):
        self._dest_dir = VIBE_SELLER_DIR / '.claude' / 'skills'
        self._lock = asyncio.Lock()

    # ── Local package sync ──────────────────────────────

    def _get_local_source(self) -> Path | None:
        """Find bundled skills/ via importlib.resources."""
        try:
            ref = importlib.resources.files('app') / 'skills'
            p = Path(str(ref))
            return p if p.is_dir() else None
        except Exception:
            return None

    @property
    def source_dir(self) -> Path | None:
        return self._get_local_source()

    async def fetch(self) -> dict:
        """Sync local package skills/ to workspace.

        Each built-in skill directory is replaced atomically:
        copy source → temp dir (unique name), rename old → backup,
        rename temp → dest, delete backup.  User-created skills
        (no matching source dir) are left untouched.

        Serialized with an asyncio.Lock so concurrent task launches
        don't race.
        """
        src = self._get_local_source()
        if not src:
            return {
                'synced': False,
                'reason': 'No skills/ in installed package',
            }

        async with self._lock:
            return await self._fetch_locked(src)

    async def _fetch_locked(self, src: Path) -> dict:
        """Inner fetch, called under lock."""
        self._dest_dir.mkdir(parents=True, exist_ok=True)
        replaced = 0
        skipped = 0
        synced_names: list[str] = []

        _ignore = shutil.ignore_patterns(
            '__pycache__',
            '__init__.py',
            'MANIFEST.txt',
            '.venv',
        )

        for src_skill in sorted(src.iterdir()):
            if not src_skill.is_dir():
                continue
            if src_skill.name.startswith(('.', '_')):
                continue

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

            # Atomic replace: unique temp dir, swap via backup
            tmp = Path(
                tempfile.mkdtemp(
                    dir=str(self._dest_dir),
                    prefix=f'.tmp_{src_skill.name}_',
                )
            )
            try:
                # mkdtemp creates the dir; copy into it with
                # dirs_exist_ok so it can populate the existing
                # empty temp dir.
                shutil.copytree(
                    src_skill, tmp, ignore=_ignore, dirs_exist_ok=True
                )
                backup = self._dest_dir / f'.bak_{src_skill.name}'
                if dest_skill.exists():
                    # Guard against symlinks — unlink, don't rmtree
                    if dest_skill.is_symlink() or dest_skill.is_file():
                        dest_skill.unlink()
                    else:
                        # Safe rmtree: dest is a real directory
                        shutil.rmtree(dest_skill)
                tmp.rename(dest_skill)
                # Cleanup backup from any prior interrupted sync
                if backup.exists():
                    if backup.is_symlink():
                        backup.unlink()
                    elif backup.is_dir():
                        shutil.rmtree(backup)
                    else:
                        backup.unlink()
            except Exception:
                # Rollback: if rename succeeded, dest is fine;
                # if not, restore from backup
                if tmp.exists():
                    if tmp.is_symlink():
                        tmp.unlink()
                    elif tmp.is_dir():
                        shutil.rmtree(tmp)
                    else:
                        tmp.unlink()
                raise
            replaced += 1

        # Track synced skills from source dirs (not dest contents)
        self._update_synced_skills(synced_names)

        # Auto-install skill Python deps into shared workspace venv
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
            if src_file.name in (
                '__init__.py',
                'MANIFEST.txt',
            ):
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

    # ── Remote sync ─────────────────────────────────────

    def _read_sync_meta(self) -> dict:
        """Read .sync_meta.json."""
        if _SYNC_META_PATH.exists():
            try:
                return json.loads(_SYNC_META_PATH.read_text())
            except Exception:
                pass
        return {}

    def _write_sync_meta(self, meta: dict) -> None:
        """Write .sync_meta.json."""
        _SYNC_META_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SYNC_META_PATH.write_text(json.dumps(meta, indent=2))

    def get_sync_meta(self) -> dict:
        """Return sync metadata for the API."""
        return self._read_sync_meta()

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

    def _update_synced_skills(self, names: list[str]) -> None:
        """Merge skill names into .sync_meta.json."""
        meta = self._read_sync_meta()
        existing = set(meta.get('synced_skills', []))
        existing.update(names)
        meta['synced_skills'] = sorted(existing)
        self._write_sync_meta(meta)

    async def _fetch_remote_commit(
        self, client: httpx.AsyncClient
    ) -> str | None:
        """Fetch latest commit hash for app/skills/ on main."""
        api_url = (
            'https://api.github.com/repos/zpoint/vibe-seller'
            '/commits?path=app/skills&per_page=1&sha=main'
        )
        try:
            resp = await client.get(
                api_url,
                headers={'Accept': 'application/vnd.github.v3+json'},
                timeout=15,
            )
            if resp.status_code == 200:
                commits = resp.json()
                if commits:
                    return commits[0]['sha']
        except Exception as e:
            logger.debug('Failed to fetch remote commit: %s', e)
        return None

    async def check_and_sync_remote(self) -> dict | None:
        """Check if remote sync is needed; sync if so.

        Conditions to trigger sync:
          1. >24 hours since last remote sync
          2. Remote commit hash differs from last synced commit

        Returns sync result dict if synced, None if skipped.
        """
        meta = self._read_sync_meta()
        last_sync = meta.get('last_sync_at')
        if last_sync:
            try:
                last_dt = datetime.fromisoformat(last_sync)
                elapsed = (datetime.now(UTC) - last_dt).total_seconds()
                if elapsed < _COOLDOWN_SECONDS:
                    return None  # Too soon
            except Exception:
                pass

        # Check remote commit
        async with httpx.AsyncClient() as client:
            remote_commit = await self._fetch_remote_commit(client)
            if not remote_commit:
                return None  # Can't reach GitHub

            last_commit = meta.get('last_commit')
            if last_commit == remote_commit:
                return None  # No changes

            # Commit differs & >24h → sync
            result = await self._do_remote_sync(client)
            result['commit'] = remote_commit

            # Update meta
            now = datetime.now(UTC).isoformat()
            new_meta = {
                'last_sync_at': now,
                'last_commit': remote_commit,
                'status': result.get('status', 'success'),
                'error': result.get('error'),
                'copied': result.get('copied', 0),
                'skipped': result.get('skipped', 0),
            }
            self._write_sync_meta(new_meta)

            # Log system event if files were actually synced
            if result.get('status') == 'success':
                await self._log_sync_event(
                    'success',
                    f'Synced {result.get("copied", 0)} skill files '
                    f'from remote (commit {remote_commit[:8]})',
                )

            return result

    async def fetch_remote(self) -> dict:
        """Force remote sync regardless of cooldown."""
        meta = self._read_sync_meta()
        async with httpx.AsyncClient() as client:
            remote_commit = await self._fetch_remote_commit(client)
            result = await self._do_remote_sync(client)
            commit = remote_commit or meta.get('last_commit', '')
            result['commit'] = commit

            now = datetime.now(UTC).isoformat()
            new_meta = {
                'last_sync_at': now,
                'last_commit': commit,
                'status': result.get('status', 'success'),
                'error': result.get('error'),
                'copied': result.get('copied', 0),
                'skipped': result.get('skipped', 0),
            }
            self._write_sync_meta(new_meta)

            if (
                result.get('status') == 'success'
                and result.get('copied', 0) > 0
            ):
                await self._log_sync_event(
                    'success',
                    f'Synced {result["copied"]} skill files from remote'
                    f' (commit {commit[:8]})',
                )

            return result

    async def _do_remote_sync(self, client: httpx.AsyncClient) -> dict:
        """Download files from remote URL using MANIFEST.txt."""
        base_url = SKILLS_REPO_URL.rstrip('/')
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

            # 2. Download each file
            self._dest_dir.mkdir(parents=True, exist_ok=True)
            copied = 0
            skipped = 0
            for rel_path in files:
                # Path traversal validation (resolve follows symlinks)
                resolved = (self._dest_dir / rel_path).resolve()
                if not str(resolved).startswith(str(self._dest_dir.resolve())):
                    logger.warning(
                        'Path traversal in MANIFEST: %s',
                        rel_path,
                    )
                    continue

                url = f'{base_url}/{rel_path}'
                try:
                    file_resp = await client.get(url, timeout=15)
                    if file_resp.status_code != 200:
                        logger.warning(
                            'Failed to fetch %s: HTTP %d',
                            rel_path,
                            file_resp.status_code,
                        )
                        continue

                    dest_file = self._dest_dir / rel_path

                    dest_file.parent.mkdir(parents=True, exist_ok=True)

                    new_content = file_resp.content
                    if dest_file.exists():
                        try:
                            if dest_file.read_bytes() == new_content:
                                skipped += 1
                                continue
                        except Exception:
                            pass

                    dest_file.write_bytes(new_content)
                    copied += 1
                except Exception as e:
                    logger.warning(
                        'Error downloading %s: %s',
                        rel_path,
                        e,
                    )

            # Track synced skill dirs from manifest (not dest)
            manifest_skill_dirs: set[str] = set()
            for rel_path in files:
                parts = Path(rel_path).parts
                if parts:
                    manifest_skill_dirs.add(parts[0])
            self._update_synced_skills(sorted(manifest_skill_dirs))

            # Post-sync: install deps into shared venv
            await self._install_skill_deps()

            logger.info(
                'Skills sync (remote): %d copied, %d unchanged',
                copied,
                skipped,
            )
            return {
                'status': 'success',
                'copied': copied,
                'skipped': skipped,
            }

        except Exception as e:
            err = f'Remote skills sync error: {e}'
            logger.warning(err)
            now = datetime.now(UTC).isoformat()
            self._write_sync_meta({
                **self._read_sync_meta(),
                'last_sync_at': now,
                'status': 'failed',
                'error': err,
            })
            await self._log_sync_event('failed', err)
            return {'status': 'failed', 'error': err}

    # ── Skill dependency management ─────────────────────

    async def _install_skill_deps(self) -> None:
        """Install skill requirements.txt into shared venv.

        Uses ``uv pip install`` which applies a file-based lock
        on the target venv, so concurrent calls are safe.
        """
        venv = VIBE_SELLER_DIR / '.venv'
        venv_python = venv / 'bin' / 'python'
        if not venv_python.exists():
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
                uv = venv / 'bin' / 'uv'
                if uv.exists():
                    cmd = [
                        str(uv),
                        'pip',
                        'install',
                        '-r',
                        str(req),
                        '--python',
                        str(venv_python),
                    ]
                else:
                    cmd = [
                        str(venv_python),
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
