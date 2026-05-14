"""Knowledge sync: copies project knowledge to local workspace.

Architecture:
  PACKAGE (app/knowledge/)             LOCAL (~/.vibe-seller/)
    common/ziniao-browser.md             knowledge/
    README.md                             project/  <- synced
                                          (root)    <- agent-generated
                                        stores/    <- per-store (local)

Three-tier sync:
  1. Local package — importlib.resources (always after pip install)
  2. Remote GitHub — fetch MANIFEST.txt + changed files
  3. On-demand — triggered before each task if >24h and commit changed

Remote sync produces a system Event when new content is synced.
"""

from datetime import UTC, datetime
import importlib.resources
import json
import logging
from pathlib import Path
import shutil

import httpx

from app.config import AI_BOT_USER_ID, KNOWLEDGE_REPO_URL
from app.database import async_session
from app.models.event import Event
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

_SYNC_META_PATH = VIBE_SELLER_DIR / 'knowledge' / '.sync_meta.json'
_COOLDOWN_SECONDS = 24 * 3600  # 24 hours


class KnowledgeSyncManager:
    """Syncs project knowledge to ~/.vibe-seller/knowledge/project/."""

    def __init__(self):
        self._dest_dir = VIBE_SELLER_DIR / 'knowledge' / 'project'

    # ── Local package sync ──────────────────────────────

    def _get_local_source(self) -> Path | None:
        """Find bundled knowledge/ via importlib.resources."""
        try:
            ref = importlib.resources.files('app') / 'knowledge'
            # Traversable → real path (works for editable installs)
            p = Path(str(ref))
            return p if p.is_dir() else None
        except Exception:
            return None

    @property
    def source_dir(self) -> Path | None:
        return self._get_local_source()

    def _read_manifest(self, src: Path) -> set[str] | None:
        """Read MANIFEST.txt and return the set of relative paths."""
        manifest = src / 'MANIFEST.txt'
        if not manifest.exists():
            return None
        lines = manifest.read_text(encoding='utf-8').splitlines()
        return {
            stripped
            for line in lines
            if (stripped := line.strip()) and not stripped.startswith('#')
        }

    async def fetch(self) -> dict:
        """Copy local package knowledge/ to workspace.

        Only files listed in MANIFEST.txt are synced.  Falls back
        to rglob if MANIFEST.txt is missing (skipping __pycache__).
        """
        src = self._get_local_source()
        if not src:
            return {
                'synced': False,
                'reason': 'No knowledge/ in installed package',
            }

        self._dest_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        skipped = 0
        manifest = self._read_manifest(src)

        if manifest is not None:
            candidates = []
            for rel in manifest:
                p = (src / rel).resolve()
                if not str(p).startswith(str(src.resolve())):
                    logger.warning('Path traversal in MANIFEST: %s', rel)
                    continue
                candidates.append(src / rel)
        else:
            candidates = [f for f in src.rglob('*') if f.is_file()]

        for src_file in candidates:
            if not src_file.is_file():
                continue
            if src_file.name.startswith('.'):
                continue
            if '__pycache__' in src_file.parts:
                continue
            rel = src_file.relative_to(src)
            dest_file = self._dest_dir / rel
            dest_file.parent.mkdir(parents=True, exist_ok=True)

            if dest_file.exists():
                try:
                    if dest_file.read_bytes() == src_file.read_bytes():
                        skipped += 1
                        continue
                except Exception:
                    pass

            shutil.copy2(src_file, dest_file)
            copied += 1

        logger.info(
            'Knowledge sync (local): %d copied, %d unchanged',
            copied,
            skipped,
        )
        return {'synced': True, 'copied': copied, 'skipped': skipped}

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

    async def _fetch_remote_commit(
        self, client: httpx.AsyncClient
    ) -> str | None:
        """Fetch latest commit hash for app/knowledge/ on main."""
        # GitHub API: get latest commit for a path
        api_url = (
            'https://api.github.com/repos/zpoint/vibe-seller'
            '/commits?path=app/knowledge&per_page=1&sha=main'
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
                    f'Synced {result.get("copied", 0)} files '
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
                    f'Synced {result["copied"]} files from remote'
                    f' (commit {commit[:8]})',
                )

            return result

    async def _do_remote_sync(self, client: httpx.AsyncClient) -> dict:
        """Download files from remote URL using MANIFEST.txt."""
        base_url = KNOWLEDGE_REPO_URL.rstrip('/')
        try:
            # 1. Fetch MANIFEST.txt
            resp = await client.get(f'{base_url}/MANIFEST.txt', timeout=15)
            if resp.status_code != 200:
                err = f'Failed to fetch MANIFEST.txt: HTTP {resp.status_code}'
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
                    logger.warning('Error downloading %s: %s', rel_path, e)

            logger.info(
                'Knowledge sync (remote): %d copied, %d unchanged',
                copied,
                skipped,
            )
            return {
                'status': 'success',
                'copied': copied,
                'skipped': skipped,
            }

        except Exception as e:
            err = f'Remote sync error: {e}'
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

    # ── Sync status (legacy compat) ─────────────────────

    def get_sync_status(self) -> dict:
        """Compare source and destination, return status."""
        src = self._get_local_source()
        if not src:
            return {
                'available': False,
                'reason': 'No knowledge/ in package',
            }

        source_files = set()
        for f in src.rglob('*'):
            if (
                f.is_file()
                and not f.name.startswith('.')
                and f.name != 'MANIFEST.txt'
            ):
                source_files.add(str(f.relative_to(src)))

        dest_files = set()
        if self._dest_dir.is_dir():
            for f in self._dest_dir.rglob('*'):
                if f.is_file() and not f.name.startswith('.'):
                    dest_files.add(str(f.relative_to(self._dest_dir)))

        missing = source_files - dest_files
        extra = dest_files - source_files
        outdated = 0
        for rel in source_files & dest_files:
            src_f = src / rel
            dst_f = self._dest_dir / rel
            try:
                if src_f.read_bytes() != dst_f.read_bytes():
                    outdated += 1
            except Exception:
                outdated += 1

        return {
            'available': True,
            'source_count': len(source_files),
            'synced_count': len(dest_files),
            'missing': len(missing),
            'outdated': outdated,
            'extra': len(extra),
            'up_to_date': len(missing) == 0 and outdated == 0,
        }

    # ── Catalog staleness + rotation ─────────────────────

    @staticmethod
    def _max_mtime(directory: Path, exclude: set[str] | None = None) -> float:
        """Return max mtime of files in directory."""
        result = 0.0
        if not directory.is_dir():
            return result
        for f in directory.rglob('*'):
            if not f.is_file() or f.name.startswith('.'):
                continue
            if f == directory / 'CATALOG.md':
                continue
            rel_parts = f.relative_to(directory).parts
            if exclude and rel_parts and rel_parts[0] in exclude:
                continue
            try:
                result = max(result, f.stat().st_mtime)
            except OSError:
                pass
        return result

    def catalog_needs_update(
        self, store_slug: str | None = None
    ) -> tuple[bool, bool]:
        """Check if L2 and/or L3 catalogs are stale.

        Returns ``(l2_stale, l3_stale)``.  A catalog is stale
        when any source file in its directory is newer than
        the catalog, or when the catalog does not exist.
        """
        knowledge_dir = VIBE_SELLER_DIR / 'knowledge'
        l2_cat = knowledge_dir / 'CATALOG.md'
        l2_stale = (
            not l2_cat.exists()
            or self._max_mtime(knowledge_dir) > l2_cat.stat().st_mtime
        )

        l3_stale = False
        if store_slug:
            store_dir = VIBE_SELLER_DIR / 'stores' / store_slug
            l3_cat = store_dir / 'CATALOG.md'
            l3_stale = (
                not l3_cat.exists()
                or self._max_mtime(store_dir) > l3_cat.stat().st_mtime
                or (
                    l2_cat.exists()
                    and l3_cat.exists()
                    and l2_cat.stat().st_mtime > l3_cat.stat().st_mtime
                )
            )

        return l2_stale, l3_stale

    @staticmethod
    def rotate_catalogs(
        store_slug: str | None = None,
        *,
        l2_stale: bool = True,
        l3_stale: bool = True,
    ) -> dict[str, tuple[Path, str]]:
        """Delete stale catalogs, keep content in memory.

        Returns ``{'l2': (path, content), 'l3': ...}`` so
        callers can restore on failure.  Files are fully
        removed — the agent cannot discover them.

        Only deletes catalogs that are actually stale to
        avoid racing with concurrent per-store tasks.
        """
        saved: dict[str, tuple[Path, str]] = {}
        if l2_stale:
            knowledge_dir = VIBE_SELLER_DIR / 'knowledge'
            l2 = knowledge_dir / 'CATALOG.md'
            if l2.exists():
                saved['l2'] = (l2, l2.read_text(encoding='utf-8'))
                l2.unlink()

        if store_slug and l3_stale:
            store_dir = VIBE_SELLER_DIR / 'stores' / store_slug
            l3 = store_dir / 'CATALOG.md'
            if l3.exists():
                saved['l3'] = (l3, l3.read_text(encoding='utf-8'))
                l3.unlink()

        return saved

    @staticmethod
    def restore_catalogs(
        saved: dict[str, tuple[Path, str]],
    ) -> None:
        """Restore catalogs from memory on agent failure."""
        for path, content in saved.values():
            if not path.exists():
                path.write_text(content, encoding='utf-8')

    @staticmethod
    def cleanup_catalog_backups(
        saved: dict[str, tuple[Path, str]],
    ) -> None:
        """No-op: content was in memory, nothing to clean."""

    # ── System event logging ────────────────────────────

    async def _log_sync_event(self, status: str, details: str) -> None:
        """Log a sync result as a system Event."""
        try:
            async with async_session() as db:
                event = Event(
                    title=f'Knowledge sync: {status}',
                    description=details,
                    status='resolved' if status == 'success' else 'open',
                    created_by=AI_BOT_USER_ID,
                    platform='system',
                )
                db.add(event)
                await db.commit()
        except Exception as e:
            logger.warning('Failed to log sync event: %s', e)


# Singleton
knowledge_sync = KnowledgeSyncManager()
