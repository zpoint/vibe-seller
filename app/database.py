import logging

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import (
    AI_BOT_USER_ID,
    DATABASE_URL,
    DEFAULT_USER_ID,
)
from app.env_options import Options
from app.models import _all_models as _all_models  # register all models
from app.models.app_settings import AppSettings
from app.models.base import Base
from app.models.schedule import Schedule
from app.models.schedule_constants import (
    SYSTEM_CATALOG_SYNC_ID,
    PhaseMode,
    StalenessCheck,
)
from app.models.user import User
from app.password import hash_password

logger = logging.getLogger(__name__)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


def _ensure_added_columns(conn) -> None:
    """Idempotently ADD columns introduced after a table first shipped.

    ``create_all`` creates missing tables but never ALTERs an existing
    one, so columns added to a model later don't appear on databases
    created before the column existed. vibe-seller has no alembic, so
    we run a tiny PRAGMA-guarded ALTER here on every boot. Each entry
    is ``(table, column, sqlite_type)`` and is skipped when the column
    is already present — safe to re-run forever.
    """
    added: list[tuple[str, str, str]] = [
        ('schedules', 'finalize_description', 'TEXT'),
        ('tasks', 'is_finalize', 'BOOLEAN NOT NULL DEFAULT 0'),
    ]
    for table, column, sqltype in added:
        cols = {
            row[1]
            for row in conn.exec_driver_sql(
                f'PRAGMA table_info({table})'
            ).fetchall()
        }
        if column not in cols:
            conn.exec_driver_sql(
                f'ALTER TABLE {table} ADD COLUMN {column} {sqltype}'
            )


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_added_columns)

    async with async_session() as session:
        # ── Seed app settings (first boot only) ──
        auth_setting = await session.get(AppSettings, 'auth_required')
        if not auth_setting:
            session.add(
                AppSettings(
                    key='auth_required',
                    value='true'
                    if Options.AUTH_REQUIRED.get_bool()
                    else 'false',
                )
            )

        # Default headless = false: agent browsers show on the desktop
        # so users see activity. Flip to true in Settings → General
        # for server/headless environments.
        headless_setting = await session.get(AppSettings, 'browser_headless')
        if not headless_setting:
            session.add(AppSettings(key='browser_headless', value='false'))

        # ── Seed admin user ──
        admin_creds_set = await session.get(
            AppSettings, 'admin_credentials_set'
        )
        force_reset = Options.FORCE_ADMIN_RESET.get_bool()

        existing_admin = await session.get(User, DEFAULT_USER_ID)
        if not existing_admin:
            # First boot — create from env
            session.add(
                User(
                    id=DEFAULT_USER_ID,
                    username=Options.ADMIN_USERNAME.get().lower(),
                    email=Options.ADMIN_EMAIL.get(),
                    password_hash=hash_password(Options.ADMIN_PASSWORD.get()),
                    role='admin',
                )
            )
        elif not admin_creds_set or force_reset:
            # Env vars still apply (never set by user, or force
            # reset requested)
            existing_admin.username = Options.ADMIN_USERNAME.get().lower()
            existing_admin.email = Options.ADMIN_EMAIL.get()
            existing_admin.password_hash = hash_password(
                Options.ADMIN_PASSWORD.get()
            )
            existing_admin.role = 'admin'
            # Clear flag on force reset so user can set again
            if force_reset and admin_creds_set:
                await session.delete(admin_creds_set)
        # If admin_credentials_set and no force_reset: don't touch

        # ── Seed AI bot user ──
        existing_bot = await session.get(User, AI_BOT_USER_ID)
        if not existing_bot:
            session.add(
                User(
                    id=AI_BOT_USER_ID,
                    username='ai_bot',
                    email='ai@vibe-seller.local',
                    password_hash='disabled',
                    role='ai_bot',
                )
            )

        await session.commit()

    # ── Seed system schedules (separate transaction) ──
    async with async_session() as session:
        catalog_sched = await session.get(
            Schedule,
            SYSTEM_CATALOG_SYNC_ID,
        )
        if not catalog_sched:
            # Fresh install — seed with all catalog-specific flags.
            session.add(
                Schedule(
                    id=SYSTEM_CATALOG_SYNC_ID,
                    title='Update Knowledge Catalogs',
                    description=(
                        'Regenerate knowledge catalogs in two '
                        'phases: L2 (global knowledge/) then '
                        'L3 (per-store). '
                        'Each level accumulates entries from '
                        'the level below. Old catalogs have '
                        'been removed — you MUST write new ones.'
                    ),
                    schedule_type='days',
                    schedule_time='03:00',
                    is_active=True,
                    plan_mode=False,
                    ai_profile_id='default',
                    is_system=True,
                    phase_mode=PhaseMode.TWO_PHASE,
                    staleness_check=StalenessCheck.CATALOG,
                    skip_reflection=True,
                    created_by=AI_BOT_USER_ID,
                )
            )
            await session.commit()


async def get_db():
    async with async_session() as session:
        yield session
