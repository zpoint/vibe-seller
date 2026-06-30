import asyncio
from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app import telemetry
from app.ai.claude_backend_manager import agent_manager
from app.browser.daemon_reaper import start_reaper_loop
from app.browser.manager import browser_manager
from app.channels import wecom_channel as wecom_channel  # registers channel
from app.config import (
    BACKEND_PORT,
    FRONTEND_DIST,
    FRONTEND_URL,
    LOCALHOST,
    LOCALHOST_NAME,
    LOG_DIR,
)
from app.database import async_session, init_db
from app.env_options import Options
from app.events_system.backends import (  # registers backends
    dida365 as dida365,
    google_calendar as google_calendar,
)
from app.models.app_settings import AppSettings
from app.models.schedule import Schedule
from app.models.store import Store
from app.models.task import Task
from app.models.user import User
from app.plugins import get_extension_context
from app.routers.app_settings import router as app_settings_router
from app.routers.attachments import router as attachments_router
from app.routers.auth import router as auth_router
from app.routers.browser import router as browser_router
from app.routers.channels import router as channels_router
from app.routers.cron import router as cron_router
from app.routers.dida365_oauth import (
    refresh_token_if_needed as _refresh_dida365_token,
    router as dida365_oauth_router,
)
from app.routers.email_accounts import router as email_accounts_router
from app.routers.email_polling import router as email_polling_router
from app.routers.events import router as events_router
from app.routers.profiles import router as profiles_router
from app.routers.schedules import router as schedules_router
from app.routers.screenshots import router as screenshots_router
from app.routers.sse import router as sse_router
from app.routers.stores import router as stores_router
from app.routers.system import router as system_router
from app.routers.tasks import router as tasks_router
from app.routers.tasks_conversation import router as tasks_conversation_router
from app.routers.tasks_files import router as tasks_files_router
from app.routers.tasks_schedule_state import (
    router as tasks_schedule_state_router,
)
from app.routers.telemetry import router as telemetry_router
from app.routers.users import router as users_router
from app.routers.wecom_bots import router as wecom_bots_router
from app.routers.workspace import router as workspace_router
from app.routers.workspace_assistant import router as ws_assistant_router
from app.routers.ziniao_accounts import router as ziniao_accounts_router
from app.scheduler.cron import (
    rebuild_schedule_jobs,
    start_scheduler,
    stop_scheduler,
)
from app.scheduler.email_sync import sync_all_email_accounts
from app.scheduler.task_queue import task_queue_scheduler
from app.telemetry_events import TelemetryEvent
from app.version import get_version
from app.workspace.knowledge_sync import knowledge_sync
from app.workspace.manager import workspace_manager
from app.workspace.skills_sync import skills_sync

log_file = LOG_DIR / f'backend_{BACKEND_PORT}.log'

_log_level = getattr(logging, Options.LOG_LEVEL.get().upper(), logging.INFO)
logging.basicConfig(
    level=_log_level,
    format='%(asctime)s %(name)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(str(log_file)),
        logging.StreamHandler(),
    ],
)


logger = logging.getLogger(__name__)


async def _apply_persisted_concurrency() -> None:
    """Read max_agent_concurrency from DB and apply to backend."""
    try:
        async with async_session() as db:
            row = await db.get(AppSettings, 'max_agent_concurrency')
            if row:
                val = max(1, min(10, int(row.value)))
                agent_manager.set_max_concurrent(val)
    except Exception:
        logger.debug('No persisted concurrency setting', exc_info=True)


async def _apply_persisted_telemetry_flag() -> None:
    """Sync the cached telemetry opt-out flag from AppSettings."""
    try:
        async with async_session() as db:
            row = await db.get(AppSettings, telemetry.SETTINGS_KEY)
            disabled = bool(row and row.value == 'false')
            telemetry.set_db_disabled(disabled)
    except Exception:
        logger.debug('No persisted telemetry flag', exc_info=True)


async def _apply_persisted_browser_headless() -> None:
    """Project ``browser_headless`` setting onto BROWSER_USE_HEADLESS env.

    The setting drives two distinct code paths:
      * The Playwright Chrome backend (`app/browser/chrome.py`) reads the
        ``headless`` value injected by ``BrowserManager`` directly from
        the DB.
      * The browser-use CLI — used for ``-aux`` sessions on Ziniao
        stores, which bypass the CDP proxy — spawns its OWN Chrome and
        honours the ``BROWSER_USE_HEADLESS`` env var. Children (agent
        subprocesses → wrapper → browser-use) inherit this server
        process's environment, so exporting it here covers both paths.
    """
    try:
        async with async_session() as db:
            row = await db.get(AppSettings, 'browser_headless')
            headless = bool(row and row.value == 'true')
        os.environ['BROWSER_USE_HEADLESS'] = 'true' if headless else 'false'
    except Exception:
        logger.debug('No persisted browser_headless setting', exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Load plugins (OSS builtin + any externally-installed plugin wheels)
    # before
    # anything reads gates / browser backends / skill sources. Idempotent
    # — module-load route wiring may have triggered it already.
    plugin_ctx = get_extension_context()
    # Apply persisted concurrency setting (overrides env default)
    await _apply_persisted_concurrency()
    await _apply_persisted_telemetry_flag()
    await _apply_persisted_browser_headless()
    telemetry.init()
    # Mark any DB sessions left as 'running' from a prior
    # server instance as idle — in-memory state is fresh.
    await browser_manager.cleanup_stale_sessions()
    start_scheduler()
    await rebuild_schedule_jobs()
    await _refresh_dida365_token()
    await task_queue_scheduler.start()
    # Workspace init at boot: dirs/symlinks + one-shot store-data
    # layout migration (old run artifacts → store-data/<YYYY-MM>/).
    # create_venv=False: a cold `uv venv` + tool bootstrap can take
    # minutes and must NOT block /api/health (it was hanging server
    # startup past the boot health-check window). The venv is built in
    # the background below; agent runs await it via their own
    # ensure_init() before launching, so nothing runs without it.
    await workspace_manager.ensure_init(create_venv=False)
    # Sync built-in knowledge + skills from package (local, no network).
    # Skill dep installs are deferred to a background task — cold pip
    # installs must not delay /api/health past CI/user boot windows.
    await knowledge_sync.fetch()
    await skills_sync.fetch(defer_deps=True)
    # Build the shared agent venv in the background (see above).
    venv_task = asyncio.create_task(workspace_manager.ensure_shared_venv())
    # Enrich app_started with rough install scale.
    try:
        async with async_session() as db_counts:
            store_count = (
                await db_counts.execute(select(func.count(Store.id)))
            ).scalar() or 0
            task_count = (
                await db_counts.execute(select(func.count(Task.id)))
            ).scalar() or 0
            schedule_count = (
                await db_counts.execute(
                    select(func.count(Schedule.id)).where(
                        Schedule.is_active.is_(True)
                    )
                )
            ).scalar() or 0
            user_count = (
                await db_counts.execute(select(func.count(User.id)))
            ).scalar() or 0
        props = telemetry.base_properties()
        props.update({
            'store_count': store_count,
            'total_task_count': task_count,
            'active_schedule_count': schedule_count,
            'user_count': user_count,
        })
    except Exception:
        props = telemetry.base_properties()
    telemetry.send(TelemetryEvent.APP_STARTED, props)
    # Trigger initial email sync in background
    sync_task = asyncio.create_task(sync_all_email_accounts())
    # Start periodic daemon reaper (kills orphaned browser-use)
    reaper_task = asyncio.create_task(start_reaper_loop())
    # Start any plugin-registered background services (e.g. a customer
    # alerting/monitoring service). Core ships none, so this is a no-op
    # in an OSS-only install. A done-callback surfaces a crashing service
    # (otherwise the exception sits unretrieved on the task until the
    # shutdown await, effectively swallowed).

    def _log_service_done(name):
        def _cb(task: asyncio.Task) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                logger.error('Plugin service %r crashed', name, exc_info=exc)

        return _cb

    service_tasks = []
    for svc_name, factory in plugin_ctx.services:
        logger.info('Starting plugin service: %s', svc_name)
        svc_task = asyncio.create_task(factory())
        svc_task.add_done_callback(_log_service_done(svc_name))
        service_tasks.append(svc_task)
    yield
    # Cancel background tasks
    for t in [sync_task, reaper_task, venv_task, *service_tasks]:
        if not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    await task_queue_scheduler.stop()
    stop_scheduler()
    telemetry.shutdown()


app = FastAPI(title='Vibe Seller', version=get_version(), lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        FRONTEND_URL,
        f'http://{LOCALHOST_NAME}:5173',
        f'http://{LOCALHOST}:5173',
        f'http://{LOCALHOST_NAME}:{BACKEND_PORT}',
        f'http://{LOCALHOST}:{BACKEND_PORT}',
    ],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# Routers
app.include_router(auth_router)
app.include_router(app_settings_router)
app.include_router(users_router)
app.include_router(stores_router)
app.include_router(tasks_router)
app.include_router(tasks_conversation_router)
app.include_router(tasks_files_router)
app.include_router(tasks_schedule_state_router)
app.include_router(telemetry_router)
app.include_router(browser_router)
app.include_router(sse_router)
app.include_router(events_router)
app.include_router(screenshots_router)
app.include_router(attachments_router)
app.include_router(workspace_router)
app.include_router(channels_router)
app.include_router(cron_router)
app.include_router(schedules_router)
app.include_router(ziniao_accounts_router)
app.include_router(email_accounts_router)
app.include_router(email_polling_router)
app.include_router(profiles_router)
app.include_router(dida365_oauth_router)
app.include_router(ws_assistant_router)
app.include_router(wecom_bots_router)
app.include_router(system_router)


@app.get('/api/health')
async def health():
    return {'status': 'ok', 'version': get_version()}


@app.get('/api/version')
async def version():
    """The running server's version — used by the UI footer and the
    Windows updater (to tell whether a newer release is available, and
    to confirm an upgrade actually took effect)."""
    return {'version': get_version()}


# Plugin wiring: mount any plugin-registered backend routers + frontend
# bundle routes BEFORE the catch-all static mount (else the SPA mount
# shadows them). Done at module load so route order is deterministic.
# OSS-only installs register none of these, so this is inert.
def _wire_plugins(fastapi_app: FastAPI) -> None:
    ctx = get_extension_context()
    for router, prefix in ctx.routers:
        if prefix:
            fastapi_app.include_router(router, prefix=prefix)
        else:
            fastapi_app.include_router(router)
    for route, handler, _early in ctx.frontend_bundles:
        fastapi_app.add_api_route(route, handler, methods=['GET'])


_wire_plugins(app)


@app.get('/api/plugins')
async def list_plugins():
    """List active plugins' frontend bundles for the dashboard loader.

    The OSS React shell fetches this and dynamically loads each
    ``js_extension_path``; ``requires_early_init`` plugins resolve before
    the app makes API calls. Returns ``[]`` in an OSS-only install (no
    frontend plugins) — the contract is live, the payload is empty.
    """
    ctx = get_extension_context()
    return [
        {'js_extension_path': route, 'requires_early_init': early}
        for route, _handler, early in ctx.frontend_bundles
    ]


# Serve frontend static files (after API routes)
if FRONTEND_DIST.exists():
    app.mount(
        '/', StaticFiles(directory=str(FRONTEND_DIST), html=True), name='static'
    )
