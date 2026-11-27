import asyncio
from datetime import UTC, datetime
import json
import logging
import shutil

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import store_platforms, telemetry
from app.auth import get_current_user
from app.browser.bookmarks import read_bookmarks, read_ziniao_bookmarks
from app.browser.inject.errors import InjectError, is_terminal
from app.browser.launch_guards import BrowserBusyError
from app.browser.manager import browser_manager
from app.browser.start_failure import (
    error_body,
    fail_task_for_start_error,
)
from app.browser.wrapper import remove_browser_use_wrapper
from app.browser.ziniao_startup import ZiniaoAccountConflictError
from app.database import get_db
from app.loop_lock import LoopLock
from app.models.browser_session import BrowserSession
from app.models.event import Event
from app.models.schedule import Schedule
from app.models.store import Store
from app.models.store_email_link import StoreEmailLink
from app.models.task import Task
from app.models.user import User
from app.scheduler.cron import remove_schedule_job
from app.schemas.store import StoreCreate, StoreResponse, StoreUpdate
from app.store_slugs import legacy_store_slug, new_store_slug
from app.task_states import ACTIVE
from app.telemetry_events import TelemetryEvent
from app.workspace.manager import VIBE_SELLER_DIR, workspace_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/stores', tags=['stores'])

# Statuses that block rename/delete
_BLOCKING_STATUSES = {s.value for s in ACTIVE} | {'queued'}

#: The name check and the write it guards run under this lock: two
#: creates arriving in the same instant must not both see the name free.
#: Folder names themselves are unique in the database (``Store.slug``,
#: app/store_slugs.py), so two stores can no longer end up sharing one.
#: Per event loop (``LoopLock``): a plain module-level ``asyncio.Lock``
#: failed whichever test contended it second.
_STORE_NAMES = LoopLock()


def _same_name(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


async def _refuse_taken_slug(
    db: AsyncSession, name: str, store_id: str | None = None
) -> None:
    """409 when another store's name already reduces to *name*'s slug.

    The rule users have always met, minus its bug. Names that are both
    plain ASCII and reduce alike ("Acme" / "acme!") are still refused,
    and so is the same name twice. A name with other characters used to
    be refused whenever its ASCII leftovers matched another store's —
    「示例甲1店」 and 「示例乙1店」 are both "1" — though nothing about
    the names is alike; it now gets a folder of its own instead
    (``new_store_slug``).
    """
    try:
        slug = legacy_store_slug(name)
    except ValueError:
        # A name with no ASCII falls back to `store-<id prefix>`, which
        # is unique by construction.
        return
    rows = (await db.execute(select(Store.id, Store.name))).all()
    for other_id, other_name in rows:
        if other_id == store_id:
            continue
        try:
            other = legacy_store_slug(other_name, other_id)
        except ValueError:
            continue
        if other != slug:
            continue
        if not (name.isascii() and other_name.isascii()) and not _same_name(
            name, other_name
        ):
            continue
        raise HTTPException(
            status_code=409,
            detail=(
                f'Name collides with existing store {other_name!r}: '
                f'both reduce to the same folder name {slug!r} '
                '(names are slugified to ASCII for on-disk paths). '
                'Choose a different name.'
            ),
        )


def _store_managed_dirs(slug: str):
    """Every on-disk dir keyed by a store's slug — the one list that
    create-purge and delete share (workspace, bin wrapper, browser
    profiles + the aux profile)."""
    return [
        workspace_manager.root / 'stores' / slug,
        VIBE_SELLER_DIR / 'bin' / slug,
        VIBE_SELLER_DIR / 'browser_profiles' / slug,
        VIBE_SELLER_DIR / 'browser_profiles' / f'{slug}-aux',
    ]


def _purge_store_dirs(slug: str) -> None:
    """Remove every slug-keyed dir (best-effort). Pure filesystem — hand
    to a thread. Used to wipe an orphaned folder before a same-name store
    is (re)created so it never inherits a deleted store's workspace/notes
    (even across a different backend), and by delete when delete_files."""
    for d in _store_managed_dirs(slug):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


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
    async with _STORE_NAMES:
        await _refuse_taken_slug(db, data.name)
        pc = store_platforms.merge_declared(
            {},
            data.platforms or None,
            data.countries or None,
            data.platform_countries,
        )
        store = Store(
            name=data.name,
            browser_backend=data.browser_backend,
            browser_config=json.dumps(data.browser_config),
            ziniao_account_id=data.ziniao_account_id,
            browser_oauth=data.browser_oauth,
        )
        store_platforms.write(store, pc)
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
            'platform_count': len(pc),
            'country_count': len(store_platforms.flat_lists(pc)[1]),
            'is_first_store': existing_count <= 1,
        },
    )

    # Create store directory + seed metadata.json + profile files
    try:
        slug = store.slug
        # A new store always starts fresh. Its slug is unique among live
        # stores (the column is), so any dir still on disk is an ORPHAN
        # left by a deleted same-slug store — wipe it (workspace/notes/wrapper/
        # profiles) before seeding so the new store never inherits the old
        # one's data, even across a different backend, and regardless of
        # whether the delete removed files. See _purge_store_dirs.
        await asyncio.to_thread(_purge_store_dirs, slug)
        store_dir = workspace_manager.root / 'stores' / slug
        store_dir.mkdir(parents=True, exist_ok=True)
        meta_file = store_dir / 'metadata.json'
        if not meta_file.exists():
            payload = json.dumps(
                {'platforms': pc},
                indent=2,
            )
            await asyncio.to_thread(meta_file.write_text, payload)
        # Scaffold profile files (STORE.md, notes, logistics,
        # browser-routing for Ziniao)
        platforms, countries = store_platforms.flat_lists(pc)
        platform = platforms[0] if platforms else ''
        country = countries[0] if countries else ''
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
    # Under the same lock as create: a rename is a claim on a slug too.
    async with _STORE_NAMES:
        return await _update_store(store_id, data, db)


async def _update_store(
    store_id: str, data: StoreUpdate, db: AsyncSession
) -> StoreResponse:
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

        await _refuse_taken_slug(db, data.name, store_id)

        # The folders follow the name, as they always have — by the same
        # rule a new store's slug is picked with, against every OTHER
        # store's slug.
        others = (
            await db.execute(
                select(Store.slug).where(
                    Store.id != store_id, Store.slug.is_not(None)
                )
            )
        ).scalars()
        old_slug = store.slug
        new_slug = new_store_slug(data.name, store.id, set(others))

        if old_slug != new_slug:
            # Scalars only into the thread — an ORM attribute read off
            # the loop could trigger a lazy load on a session that is
            # not thread-safe.
            await asyncio.to_thread(
                _rename_store_dirs, old_slug, new_slug, store.name, data.name
            )
            store.slug = new_slug

        store.name = data.name

    if data.browser_config is not None:
        store.browser_config = json.dumps(data.browser_config)
    if (
        data.platforms is not None
        or data.countries is not None
        or data.platform_countries is not None
    ):
        store_platforms.write(
            store,
            store_platforms.merge_declared(
                store_platforms.parse_platform_countries(
                    store.platform_countries
                ),
                data.platforms,
                data.countries,
                data.platform_countries,
            ),
        )

    store.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    await db.refresh(store)
    telemetry.send(TelemetryEvent.STORE_UPDATED, {'store_id': store_id})
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

    slug = store.slug
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

    # Delete files if requested (non-blocking). One helper removes every
    # slug-keyed dir (workspace, bin wrapper, browser profiles + aux) for
    # all backends — the per-backend branches here used to miss bin/<slug>/.
    if delete_files:
        await asyncio.to_thread(_purge_store_dirs, slug)

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
        return read_bookmarks(store.slug)
    if store.browser_backend == 'ziniao' and store.browser_oauth:
        return read_ziniao_bookmarks(store.browser_oauth)
    return []


@router.post('/{store_id}/browser/start')
async def start_browser(
    store_id: str,
    # Optional so the OPERATOR MCP (``app/agent_mcp/tools_stores.py``,
    # served at ``/api/mcp``) can call this route directly — its
    # ``call_route`` supplies no Request; over HTTP FastAPI always
    # injects the real one.
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Start the browser session for a store (lazy startup).

    A Ziniao store's client is brought up first: when it is not
    answering (normal mode, not running, leftovers) it is killed and
    relaunched in WebDriver mode — always, no flag. Old wrappers still
    send ``?force=1``; it is ignored.

    A start failure that carries the inject taxonomy reaches the
    caller as a TYPED body (``detail.code`` / ``message`` / ``hint``).
    When the request names the task it serves (the wrapper sends
    ``X-Vibe-Task``) and the failure is terminal, the task is ended
    FAILED with the code + category and the agent is stopped — the
    operator must fix this class of failure, and a bare 500 taught
    agents to improvise (incident 97db66d2). The header only ever
    SELECTS a task: it must be this store's own live run, or it is
    ignored.
    """
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail='Store not found')
    try:
        session = await browser_manager.start_session(store, db)
    except BrowserBusyError as e:
        # 503, not 500: nothing is known to be wrong with THIS store —
        # somebody else holds the launch lock, or Ziniao is still
        # starting. A hang here is what the caller used to get instead,
        # and `HTTP 000` after 240s is not something an agent or an
        # operator can act on. The message says what to wait for.
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ZiniaoAccountConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except InjectError as e:
        if is_terminal(e):
            # The wrapper names the task it is starting the browser
            # for; a UI-driven start sends no header and only gets the
            # typed body. A direct call from the operator MCP has NO
            # request at all — guard it, or the typed body would be
            # replaced by an AttributeError traceback. The FAILED
            # commit happens before the agent stop, mirroring the
            # user-stop route.
            task_id = (
                request.headers.get('X-Vibe-Task', '') if request else ''
            ).strip()
            if task_id:
                await fail_task_for_start_error(
                    task_id, e, store.id, store.name
                )
        raise HTTPException(status_code=500, detail=error_body(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to start browser: {e}',
        ) from e
    # The typed fields let the store wrapper follow a REALLOCATED proxy
    # port: the allocator moves a store off a port held by another
    # server instance, and the wrapper that triggered this call is
    # still running with the old port baked in (PR C1).
    return {
        'ok': True,
        'proxy_port': getattr(session, 'proxy_port', None),
        'instance_id': browser_manager.instance_id,
    }


def _to_response(store: Store) -> dict:
    pc = store_platforms.parse_platform_countries(store.platform_countries)
    platforms, countries = store_platforms.flat_lists(pc)
    return {
        'id': store.id,
        'name': store.name,
        'slug': store.slug,
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


def _rename_store_dirs(
    old_slug: str,
    new_slug: str,
    old_name: str,
    new_name: str,
) -> None:
    """Move a renamed store's on-disk dirs and rewrite .md references.

    Pure filesystem work (dir renames, a tree walk, wrapper removal),
    grouped into one sync helper so the route can hand it to a thread
    with a single ``asyncio.to_thread`` hop. Takes plain scalars —
    never the ORM instance.

    Every slug-keyed dir moves, whatever the backend: the run data under
    ``store-data/`` and an anti-detect store's aux profile used to stay
    behind under the old name, where the renamed store no longer looked.
    """
    root = workspace_manager.root
    profiles = VIBE_SELLER_DIR / 'browser_profiles'
    for old, new in (
        (root / 'stores' / old_slug, root / 'stores' / new_slug),
        (root / 'store-data' / old_slug, root / 'store-data' / new_slug),
        (profiles / old_slug, profiles / new_slug),
        (profiles / f'{old_slug}-aux', profiles / f'{new_slug}-aux'),
    ):
        if old.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)

    # Find-replace old name → new name in .md files
    _replace_name_in_md_files(root / 'stores' / new_slug, old_name, new_name)

    # Remove old wrapper (re-add on next session start)
    remove_browser_use_wrapper(old_slug)


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
