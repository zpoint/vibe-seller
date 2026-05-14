import asyncio
from datetime import UTC, datetime
import json
import logging
import shutil

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry
from app.auth import get_current_user
from app.browser.bookmarks import read_bookmarks, read_ziniao_bookmarks
from app.browser.manager import (
    browser_manager,
    store_slug as _store_slug,
)
from app.database import get_db
from app.models.browser_session import BrowserSession
from app.models.event import Event
from app.models.schedule import Schedule
from app.models.store import Store
from app.models.store_email_link import StoreEmailLink
from app.models.task import Task
from app.models.user import User
from app.scheduler.cron import remove_schedule_job
from app.schemas.store import StoreCreate, StoreResponse, StoreUpdate
from app.task_states import ACTIVE
from app.telemetry_events import TelemetryEvent
from app.workspace.manager import VIBE_SELLER_DIR, workspace_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/stores', tags=['stores'])

# Statuses that block rename/delete
_BLOCKING_STATUSES = {s.value for s in ACTIVE} | {'queued'}


@router.get('', response_model=list[StoreResponse])
async def list_stores(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Sort stores by most recent task update (active stores first)
    latest_task = (
        select(
            Task.store_id,
            func.max(Task.updated_at).label('last_task_at'),
        )
        .where(Task.store_id.is_not(None))
        .group_by(Task.store_id)
        .subquery()
    )
    result = await db.execute(
        select(Store)
        .outerjoin(latest_task, Store.id == latest_task.c.store_id)
        .order_by(
            # COALESCE: stores with no tasks get '' which sorts last
            func.coalesce(latest_task.c.last_task_at, '').desc(),
            Store.created_at.desc(),
        )
    )
    stores = result.scalars().all()
    return [_to_response(s) for s in stores]


@router.post('', response_model=StoreResponse)
async def create_store(
    data: StoreCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    store = Store(
        name=data.name,
        browser_backend=data.browser_backend,
        browser_config=json.dumps(data.browser_config),
        ziniao_account_id=data.ziniao_account_id,
        browser_oauth=data.browser_oauth,
        platforms=json.dumps(data.platforms),
        countries=json.dumps(data.countries),
        platform_countries=json.dumps(data.platform_countries),
    )
    db.add(store)
    await db.commit()
    await db.refresh(store)

    existing_count = (
        await db.execute(select(func.count()).select_from(Store))
    ).scalar() or 0
    telemetry.send(
        TelemetryEvent.STORE_CREATED,
        {
            'browser_backend': data.browser_backend,
            'platform_count': len(data.platforms or []),
            'country_count': len(data.countries or []),
            'is_first_store': existing_count <= 1,
        },
    )

    # Create store directory + seed metadata.json + profile files
    try:
        slug = _store_slug(data.name, store.id)
        store_dir = workspace_manager.root / 'stores' / slug
        store_dir.mkdir(parents=True, exist_ok=True)
        meta_file = store_dir / 'metadata.json'
        if not meta_file.exists():
            meta_file.write_text(
                json.dumps(
                    {'platforms': data.platform_countries},
                    indent=2,
                )
            )
        # Scaffold profile files (STORE.md, notes, logistics,
        # browser-routing for Ziniao)
        platform = data.platforms[0] if data.platforms else ''
        country = data.countries[0] if data.countries else ''
        await workspace_manager.create_store_profile(
            slug=slug,
            name=data.name,
            platform=platform,
            country=country,
            backend=data.browser_backend,
        )
    except Exception as e:
        logger.warning(f'Failed to create store workspace dir: {e}')

    return _to_response(store)


@router.put('/{store_id}', response_model=StoreResponse)
async def update_store(
    store_id: str,
    data: StoreUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail='Store not found')

    # Handle name change with slug rename
    if data.name is not None and data.name != store.name:
        # Block rename if active tasks
        active_count = (
            await db.execute(
                select(func.count())
                .select_from(Task)
                .where(
                    Task.store_id == store_id,
                    Task.status.in_(_BLOCKING_STATUSES),
                )
            )
        ).scalar_one()
        if active_count > 0:
            raise HTTPException(
                status_code=409,
                detail='Cannot rename store with active tasks',
            )

        # Block if active (running) browser session
        session = (
            await db.execute(
                select(BrowserSession).where(
                    BrowserSession.store_id == store_id,
                    BrowserSession.status == 'running',
                )
            )
        ).scalar_one_or_none()
        if session:
            raise HTTPException(
                status_code=409,
                detail='Cannot rename store with active browser'
                ' session. Stop the browser first.',
            )

        # Check name uniqueness
        existing = (
            await db.execute(
                select(Store).where(
                    Store.name == data.name, Store.id != store_id
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=409,
                detail='A store with this name already exists',
            )

        old_slug = _store_slug(store.name, store.id)
        new_slug = _store_slug(data.name, store.id)

        if old_slug != new_slug:
            # Rename store knowledge dir
            old_store_dir = workspace_manager.root / 'stores' / old_slug
            new_store_dir = workspace_manager.root / 'stores' / new_slug
            if old_store_dir.exists():
                new_store_dir.parent.mkdir(parents=True, exist_ok=True)
                old_store_dir.rename(new_store_dir)
                # Find-replace old name → new name in .md files
                _replace_name_in_md_files(new_store_dir, store.name, data.name)

            # Rename browser profile dir (Chrome only)
            if store.browser_backend == 'chrome':
                old_profile = VIBE_SELLER_DIR / 'browser_profiles' / old_slug
                new_profile = VIBE_SELLER_DIR / 'browser_profiles' / new_slug
                if old_profile.exists():
                    new_profile.parent.mkdir(parents=True, exist_ok=True)
                    old_profile.rename(new_profile)

            # Rename aux Chrome profile dir (Ziniao only)
            if store.browser_backend == 'ziniao':
                old_aux = (
                    VIBE_SELLER_DIR / 'browser_profiles' / f'{old_slug}-aux'
                )
                new_aux = (
                    VIBE_SELLER_DIR / 'browser_profiles' / f'{new_slug}-aux'
                )
                if old_aux.exists():
                    new_aux.parent.mkdir(parents=True, exist_ok=True)
                    old_aux.rename(new_aux)

            # Remove old wrapper (re-add on next session start)
            browser_manager.remove_browser_entry(
                store.name, store.browser_backend, store.id
            )

        store.name = data.name

    if data.browser_config is not None:
        store.browser_config = json.dumps(data.browser_config)
    if data.platforms is not None:
        store.platforms = json.dumps(data.platforms)
    if data.countries is not None:
        store.countries = json.dumps(data.countries)
    if data.platform_countries is not None:
        store.platform_countries = json.dumps(data.platform_countries)

    store.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    await db.refresh(store)
    return _to_response(store)


@router.get('/{store_id}', response_model=StoreResponse)
async def get_store(
    store_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail='Store not found')
    return _to_response(store)


@router.delete('/{store_id}')
async def delete_store(
    store_id: str,
    delete_files: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    store = await db.get(Store, store_id)
    if not store:
        return {'ok': True}

    # Block if active tasks
    active_count = (
        await db.execute(
            select(func.count())
            .select_from(Task)
            .where(
                Task.store_id == store_id,
                Task.status.in_(_BLOCKING_STATUSES),
            )
        )
    ).scalar_one()
    if active_count > 0:
        raise HTTPException(
            status_code=409,
            detail='Cannot delete store with active tasks',
        )

    # Capture telemetry snapshot BEFORE the cascading deletes wipe the
    # rows we want to count.
    task_count_at_deletion = (
        await db.execute(
            select(func.count())
            .select_from(Task)
            .where(Task.store_id == store_id)
        )
    ).scalar_one()

    # Stop browser session and remove .mcp.json entry
    await browser_manager.stop_session(store, db)

    # Clean up referencing tables (no cascade in SQLite)
    await db.execute(
        delete(StoreEmailLink).where(StoreEmailLink.store_id == store_id)
    )
    await db.execute(
        delete(BrowserSession).where(BrowserSession.store_id == store_id)
    )

    # Delete schedules + remove APScheduler jobs
    schedule_rows = (
        (
            await db.execute(
                select(Schedule.id).where(Schedule.store_id == store_id)
            )
        )
        .scalars()
        .all()
    )
    for sid in schedule_rows:
        remove_schedule_job(sid)
    await db.execute(delete(Schedule).where(Schedule.store_id == store_id))

    # Delete tasks belonging to this store
    await db.execute(delete(Task).where(Task.store_id == store_id))
    # Nullify event references (preserve history)
    await db.execute(
        update(Event).where(Event.store_id == store_id).values(store_id=None)
    )

    slug = _store_slug(store.name, store.id)
    age_days: float | None = None
    if store.created_at:
        try:
            age_days = (
                datetime.now(UTC) - datetime.fromisoformat(store.created_at)
            ).total_seconds() / 86400
        except (ValueError, TypeError):
            age_days = None

    await db.delete(store)
    await db.commit()

    telemetry.send(
        TelemetryEvent.STORE_DELETED,
        {
            'browser_backend': store.browser_backend,
            'task_count_at_deletion_bucket': telemetry.count_bucket(
                task_count_at_deletion or 0
            ),
            'age_bucket': telemetry.age_bucket_days(age_days),
        },
    )

    # Delete files if requested (non-blocking)
    if delete_files:
        store_dir = workspace_manager.root / 'stores' / slug
        if store_dir.exists():
            await asyncio.to_thread(shutil.rmtree, store_dir)

        if store.browser_backend == 'chrome':
            profile_dir = VIBE_SELLER_DIR / 'browser_profiles' / slug
            if profile_dir.exists():
                await asyncio.to_thread(shutil.rmtree, profile_dir)

        if store.browser_backend == 'ziniao':
            aux_dir = VIBE_SELLER_DIR / 'browser_profiles' / f'{slug}-aux'
            if aux_dir.exists():
                await asyncio.to_thread(shutil.rmtree, aux_dir)

    return {'ok': True}


@router.get('/{store_id}/bookmarks')
async def get_store_bookmarks(
    store_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Read bookmarks from a store's browser profile."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail='Store not found')
    if store.browser_backend == 'chrome':
        return read_bookmarks(_store_slug(store.name, store.id))
    if store.browser_backend == 'ziniao' and store.browser_oauth:
        return read_ziniao_bookmarks(store.browser_oauth)
    return []


@router.post('/{store_id}/browser/start')
async def start_browser(
    store_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Start the browser session for a store (lazy startup)."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail='Store not found')
    try:
        await browser_manager.start_session(store, db)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to start browser: {e}',
        ) from e
    return {'ok': True}


def _to_response(store: Store) -> dict:
    pc = (
        json.loads(store.platform_countries) if store.platform_countries else {}
    )
    # Compute platforms/countries from platform_countries for
    # backward compat; fall back to legacy columns if pc is empty.
    if pc:
        platforms = list(pc.keys())
        countries = sorted({c for cs in pc.values() for c in cs})
    else:
        platforms = json.loads(store.platforms) if store.platforms else []
        countries = json.loads(store.countries) if store.countries else []
    return {
        'id': store.id,
        'name': store.name,
        'browser_backend': store.browser_backend,
        'browser_config': json.loads(store.browser_config)
        if store.browser_config
        else {},
        'ziniao_account_id': store.ziniao_account_id,
        'browser_oauth': store.browser_oauth,
        'platforms': platforms,
        'countries': countries,
        'platform_countries': pc,
        'created_at': store.created_at,
        'updated_at': store.updated_at,
    }


def _replace_name_in_md_files(directory, old_name: str, new_name: str):
    """Replace store name references in .md files."""
    if not directory.exists():
        return
    for md_file in directory.rglob('*.md'):
        try:
            content = md_file.read_text(encoding='utf-8')
            if old_name in content:
                md_file.write_text(
                    content.replace(old_name, new_name),
                    encoding='utf-8',
                )
        except Exception as e:
            logger.warning('Failed to update %s: %s', md_file, e)
