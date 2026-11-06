"""Knowledge sync: copies project knowledge to local workspace.

Architecture:
  PACKAGE (app/knowledge/)             LOCAL (~/.vibe-seller/)
    common/ziniao-browser.md             knowledge/
    README.md                             project/  <- synced
                                          (root)    <- agent-generated
                                        stores/    <- per-store (local)

Two sync tiers (mirrors skills_sync.py):
  1. Local package — importlib.resources, once at server boot
  2. Remote GitHub — MANIFEST.txt + changed files, driven by the
     central sync scheduler's ``knowledge`` job (app/sync/jobs.py)

WHEN the remote tier runs is the scheduler's ``sync_state`` row — there
is no per-module cooldown file any more. Remote sync produces a system
Event when new content is synced.
"""

import importlib.resources
import logging
import os
from pathlib import Path
import shutil
import uuid

import httpx

from app.config import AI_BOT_USER_ID, KNOWLEDGE_REPO_URL
from app.database import async_session
from app.models.event import Event
from app.sync import github as github_probe
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)


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

    async def probe_remote(
        self, client: httpx.AsyncClient, etag: str | None = None
    ) -> dict:
        """Ask GitHub for the latest commit of ``app/knowledge``.

        See :func:`app.sync.github.probe_commit`.
        """
        return await github_probe.probe_commit(
            client, 'app/knowledge', etag=etag
        )

    async def do_remote_sync(self, client: httpx.AsyncClient) -> dict:
        """Download files from remote URL using MANIFEST.txt.

        Local-package precedence: files that exist in the installed
        package are authoritative for the package version's lifetime.
        Remote sync only fills in files NOT present locally. See
        ``skills_sync.do_remote_sync`` for the rationale.

        Every network read happens before the first disk write; the
        writes themselves are rename-atomic and run in one synchronous
        block, so a reader never sees a half-written file.
        """
        base_url = KNOWLEDGE_REPO_URL.rstrip('/')
        local_source = self._get_local_source()
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
            if not files:
                err = 'MANIFEST.txt is empty; refusing to delete'
                logger.warning(err)
                return {
                    'status': 'failed',
                    'error': err,
                    'copied': 0,
                    'skipped': 0,
                    'deleted': 0,
                }

            # 2. Download into memory (all awaits end here).
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

                # Local-package precedence: skip files that exist
                # in the installed package — local is authoritative.
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

            # 3. Mirror upstream. ``knowledge/project/`` is the synced
            # subtree and nothing else is touched (the agent writes the
            # knowledge root and stores/ itself): a file upstream
            # dropped goes away here, so synced content cannot outlive
            # what it was synced from.
            self._dest_dir.mkdir(parents=True, exist_ok=True)
            package = self._package_files()
            desired = dict(package)
            desired.update(downloads)
            existing = self._existing_files()
            # A listed file that failed to fetch is still upstream's:
            # keep the local copy and delete nothing this run.
            for rel in listed - set(downloads):
                if rel not in package and rel in existing:
                    desired[rel] = existing[rel].read_bytes()
            doomed = (
                []
                if failed
                else sorted(
                    rel
                    for rel in existing
                    if rel not in listed and rel not in package
                )
            )
            if doomed and len(doomed) * 2 > max(len(existing), 1):
                err = (
                    f'refusing mass delete ({len(doomed)} of '
                    f'{len(existing)} knowledge files)'
                )
                logger.warning(err)
                return {
                    'status': 'failed',
                    'error': err,
                    'copied': 0,
                    'skipped': 0,
                    'deleted': 0,
                }

            copied = 0
            skipped = 0
            for rel, content in sorted(desired.items()):
                if rel in package and rel not in downloads:
                    # The local fetch owns package files.
                    skipped += 1
                    continue
                if self._install_file(self._dest_dir / rel, content):
                    copied += 1
                else:
                    skipped += 1
            for rel in doomed:
                self._remove(self._dest_dir / rel)

            logger.info(
                'Knowledge sync (remote): %d copied, %d unchanged, %d deleted',
                copied,
                skipped,
                len(doomed),
            )
            if copied:
                await self._log_sync_event(
                    'success',
                    f'Synced {copied} files from remote',
                )
            if failed:
                err = f'{len(failed)} files failed to download'
                logger.warning('Knowledge sync (remote): %s', err)
                return {
                    'status': 'failed',
                    'error': err,
                    'copied': copied,
                    'skipped': skipped,
                    'deleted': 0,
                }
            return {
                'status': 'success',
                'copied': copied,
                'skipped': skipped,
                'deleted': len(doomed),
            }

        except Exception as e:
            err = f'Remote sync error: {e}'
            logger.warning(err)
            await self._log_sync_event('failed', err)
            return {'status': 'failed', 'error': err}

    # ── Staging (atomic file writes) ────────────────────

    @property
    def _staging_root(self) -> Path:
        """Where a file waits for its rename (a sibling, never in-tree)."""
        return self._dest_dir.parent / '.sync-staging'

    def sweep_staging(self) -> None:
        """Drop half-written files left by an interrupted sync."""
        shutil.rmtree(self._staging_root, ignore_errors=True)

    def _package_files(self) -> dict[str, bytes]:
        """Files the local package ships (the package stays authoritative)."""
        src_dir = self._get_local_source()
        out: dict[str, bytes] = {}
        if not src_dir:
            return out
        manifest = self._read_manifest(src_dir)
        rels = (
            [src_dir / rel for rel in manifest]
            if manifest is not None
            else [f for f in src_dir.rglob('*') if f.is_file()]
        )
        for f in rels:
            if not f.is_file() or f.name.startswith('.'):
                continue
            if '__pycache__' in f.parts:
                continue
            out[f.relative_to(src_dir).as_posix()] = f.read_bytes()
        return out

    def _existing_files(self) -> dict[str, Path]:
        out: dict[str, Path] = {}
        if not self._dest_dir.is_dir():
            return out
        for f in sorted(self._dest_dir.rglob('*')):
            if not f.is_file() or f.name.startswith('.'):
                continue
            out[f.relative_to(self._dest_dir).as_posix()] = f
        return out

    @staticmethod
    def _remove(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)

    def _install_file(self, target: Path, content: bytes) -> bool:
        """Atomically write one file; ``False`` when already identical."""
        if target.exists():
            try:
                if target.read_bytes() == content:
                    return False
            except OSError:
                pass
        root = self._staging_root
        root.mkdir(parents=True, exist_ok=True)
        tmp = root / f'file-{uuid.uuid4().hex}'
        tmp.write_bytes(content)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, target)
        return True

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
