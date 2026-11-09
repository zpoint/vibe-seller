"""The store's platform list — kept by the server, never by an agent."""

import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.manager import store_slug as _store_slug
from app.models.store import Store
from app.models.task import Task
from app.workspace.manager import workspace_manager

logger = logging.getLogger(__name__)


def _record_platform(meta_path: Path, platform: str, country: str) -> None:
    """Add one platform/country to a store's metadata.json."""
    try:
        data = json.loads(meta_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or not isinstance(
        data.get('platforms'), dict
    ):
        data = {'platforms': {}}
    countries = data['platforms'].setdefault(platform, [])
    if country and country not in countries:
        countries.append(country)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(data, indent=2), encoding='utf-8')


async def sync_store_metadata(
    store_id: str, db: AsyncSession, task_id: str | None = None
):
    """Keep the store's platform list current after a task.

    The server owns ``metadata.json``: the task's own platform/country
    is recorded here, then the file is merged into
    ``Store.platform_countries``. Agents never write it -- that duty once
    kept a trivial task running minutes past its answer.
    """
    try:
        store = await db.get(Store, store_id)
        if not store:
            return
        slug = _store_slug(store.name, store.id)
        meta_path = workspace_manager.root / 'stores' / slug / 'metadata.json'
        task = await db.get(Task, task_id) if task_id else None
        if task is not None and task.platform:
            await asyncio.to_thread(
                _record_platform,
                meta_path,
                task.platform.strip().lower(),
                (task.country or '').strip().upper(),
            )
        if not meta_path.exists():
            return
        raw = await asyncio.to_thread(meta_path.read_text, encoding='utf-8')
        data = json.loads(raw)
        if not isinstance(data, dict):
            logger.warning('metadata.json not a dict for %s', slug)
            return
        file_platforms = data.get('platforms', {})
        if not isinstance(file_platforms, dict):
            logger.warning(
                'metadata.json platforms not a dict for %s',
                slug,
            )
            return

        # Sanitize + merge
        existing = (
            json.loads(store.platform_countries)
            if store.platform_countries
            else {}
        )
        count = 0
        for platform, countries in file_platforms.items():
            if not isinstance(countries, list):
                continue
            p = platform.strip().lower()
            if not p or count >= 50:
                break
            cs = []
            for c in countries:
                if isinstance(c, str):
                    cv = c.strip().upper()
                    if cv and len(cs) < 100:
                        cs.append(cv)
            existing.setdefault(p, [])
            existing[p] = sorted(set(existing[p]) | set(cs))
            count += 1

        store.platform_countries = json.dumps(existing)
        await db.commit()
    except Exception as e:
        logger.warning(
            'Metadata sync failed for store %s: %s',
            store_id,
            e,
        )
