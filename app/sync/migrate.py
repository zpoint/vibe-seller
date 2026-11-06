"""One-shot import of the pre-scheduler sync bookkeeping.

Older releases tracked "when did this last sync?" in per-module files:
``.claude/skills/.sync_meta.json``, ``knowledge/.sync_meta.json`` and a
bookkeeping header in ``cache/provider_presets.json``. The central sync
scheduler keeps it in the ``sync_state`` table instead, so a restart
computes the next run from the database rather than from whatever a
file happened to say.

This runs at every boot and is idempotent:

* an existing ``sync_state`` row wins — the import never overwrites it;
* a corrupt / partial / missing legacy file is treated as "never
  synced" and must not fail the boot;
* the legacy files are left INVENTORY-only (skills) or deleted
  (knowledge), and the presets cache keeps nothing but catalog data.

Kept permanently: any old version may jump straight to this one.

The migration reads the legacy files itself (rather than asking the
managers) precisely because those readers are gone — see
``app/workspace/knowledge_sync.py``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.sync import state as sync_state
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger('app.sync')

#: Legacy schedule keys. Everything else in the skills meta file is
#: inventory (``synced_skills``, ``gws_installed``, ``installed_deps``)
#: and stays where it is.
_SKILLS_SCHEDULE_KEYS = (
    'last_sync_at',
    'last_commit',
    'status',
    'error',
    'copied',
    'skipped',
)

_PRESETS_CACHE = 'cache/provider_presets.json'


def _read_json(path: Path) -> dict | None:
    """The parsed file, or ``None`` when missing or corrupt."""
    try:
        blob = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return blob if isinstance(blob, dict) else None


#: What the old writers put in ``status`` when the sync actually
#: landed. Anything else (``failed`` above all) is a run that did not.
#: A file with NO status is old enough to predate the field, and those
#: runs only ever wrote it on success.
_SUCCESS = ('success', 'ok')


def _mapping(meta: dict, *, commit_key: str = 'last_commit') -> dict:
    """Translate one legacy meta dict into ``record_import()`` fields.

    The legacy files were written on FAILED runs too — ``last_sync_at``,
    ``last_commit`` and ``status='failed'``, where ``last_commit`` is an
    upstream commit whose content never landed. Importing that as
    ``last_ok_at`` + ``version`` would be doubly wrong: the failure
    would count as a successful run for scheduling, and the next probe
    would see "remote == version" and never fetch the content at all.
    So only a success-like row carries the timestamp and the commit;
    a failed one keeps just ``last_run_at`` / ``status`` / ``error``,
    which leaves the job overdue and reaching for the download again.

    Returns ``{}`` when the file carries nothing schedule-shaped — a
    half-written file is "never synced", not "synced at the epoch".
    """
    detail = {}
    for key in ('copied', 'skipped'):
        if isinstance(meta.get(key), int):
            detail[key] = meta[key]
    status = meta.get('status')
    when = meta.get('last_sync_at')
    ok = status is None or status in _SUCCESS
    return {
        'status': status,
        'error': meta.get('error'),
        'version': meta.get(commit_key) if ok else None,
        'last_ok_at': when if ok else None,
        'last_run_at': when,
        'detail': detail or None,
    }


async def _import_row(job_name: str, meta: dict) -> bool:
    """Import one legacy meta dict; ``False`` when it changed nothing."""
    if await sync_state.get_row(job_name) is not None:
        return False
    fields = _mapping(meta)
    if not any(
        fields.get(key) is not None
        for key in (
            'last_ok_at',
            'last_run_at',
            'version',
            'status',
            'error',
            'detail',
        )
    ):
        return False
    await sync_state.record_import(job_name, **fields)
    return True


async def migrate_skills_meta() -> bool:
    """Trim the skills meta file to inventory; import its schedule state."""
    path = VIBE_SELLER_DIR / '.claude' / 'skills' / '.sync_meta.json'
    meta = _read_json(path)
    if meta is None:
        return False
    imported = await _import_row('skills', meta)
    if any(key in meta for key in _SKILLS_SCHEDULE_KEYS):
        remaining = {
            key: value
            for key, value in meta.items()
            if key not in _SKILLS_SCHEDULE_KEYS
        }
        try:
            path.write_text(json.dumps(remaining, indent=2), encoding='utf-8')
        except OSError:
            logger.warning('Could not trim %s', path, exc_info=True)
    return imported


async def migrate_knowledge_meta() -> bool:
    """Import the knowledge meta file, then delete it.

    The knowledge tree has no inventory file — the whole file was
    schedule state, so it goes away (the UI reads ``GET /api/sync/status``
    now; ``get_sync_status`` compares trees and never needed it).
    """
    path = VIBE_SELLER_DIR / 'knowledge' / '.sync_meta.json'
    meta = _read_json(path)
    if meta is None:
        return False
    imported = await _import_row('knowledge', meta)
    try:
        path.unlink()
    except OSError:
        logger.warning('Could not remove %s', path, exc_info=True)
    return imported


async def migrate_presets_cache() -> bool:
    """Move the presets cache's bookkeeping into ``sync_state``.

    The cache file keeps the catalog (and only the catalog):
    ``current_presets()`` reads it at request time and must stay
    file-backed and synchronous.
    """
    path = VIBE_SELLER_DIR / _PRESETS_CACHE
    blob = _read_json(path)
    if blob is None:
        return False
    detail = {}
    if isinstance(blob.get('source_url'), str):
        detail['source_url'] = blob['source_url']
    bookkeeping = {
        'sha256': blob.get('sha256')
        if isinstance(blob.get('sha256'), str)
        else None,
        'etag': blob.get('etag') if isinstance(blob.get('etag'), str) else None,
        'detail': detail or None,
        'last_ok_at': blob.get('fetched_at'),
        'status': None,
        'error': None,
    }
    if not any(
        bookkeeping.get(key) is not None
        for key in ('sha256', 'etag', 'detail', 'last_ok_at')
    ):
        return False
    imported = False
    if await sync_state.get_row('presets') is None and any(
        bookkeeping.get(key) is not None
        for key in ('sha256', 'etag', 'detail', 'last_ok_at')
    ):
        await sync_state.record_import(
            'presets',
            last_ok_at=bookkeeping['last_ok_at'],
            version=bookkeeping['sha256'],
            etag=bookkeeping['etag'],
            detail=bookkeeping['detail'],
        )
        imported = True
    # Rewrite the cache as catalog-only, whatever the row said.
    catalog = {key: blob[key] for key in ('presets', 'models') if key in blob}
    if catalog:
        try:
            tmp = path.with_name(path.name + '.tmp')
            tmp.write_text(json.dumps(catalog, indent=2), encoding='utf-8')
            tmp.replace(path)
        except OSError:
            logger.warning('Could not trim %s', path, exc_info=True)
    return imported


async def migrate_legacy_state() -> dict:
    """Import every legacy location. Safe to run on every boot."""
    done = {
        'skills': await migrate_skills_meta(),
        'knowledge': await migrate_knowledge_meta(),
        'presets': await migrate_presets_cache(),
    }
    if any(done.values()):
        logger.info('Imported legacy sync bookkeeping: %s', done)
    return done
