"""Daily install heartbeat with the install's inventory counts.

Events only catch configuration done after telemetry v3 shipped, so
the heartbeat also carries the CURRENT state: enum and count values
only — never names, URLs, keys or models (the one deliberate
exception is a custom provider's user-typed display name and its
base URL's HOST, see ``app.ai.profiles.custom_profile_labels``).
"""

from datetime import UTC, datetime, timedelta
import json
import logging
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import func, select

from app import telemetry
from app.ads_client import api_calls_last_24h
from app.ai.profiles import (
    ProfileManager,
    custom_provider_entries,
    default_provider_hosts,
    profile_kind,
    profile_kind_for_id,
    provider_hosts,
)
from app.database import async_session
from app.events_system.syncer import load_backend_config
from app.models.api_token import ApiToken
from app.models.app_settings import AppSettings
from app.models.chat_bot import ChatBot
from app.models.email_account import EmailAccount
from app.models.schedule import Schedule
from app.models.store import Store
from app.models.task import Task
from app.models.user import User
from app.release_channel import channel_of
from app.sync.scheduler import sync_scheduler
from app.telemetry_events import TelemetryEvent
from app.telemetry_mcp import mcp_calls_last_24h
from app.utils.timezone import get_server_timezone
from app.vision import get_kie_api_key

logger = logging.getLogger(__name__)

# The startup fire waits this long after registration so it reports a
# settled install (DB open, jobs rebuilt) instead of racing them.
STARTUP_DELAY_S = 90


def register_install_heartbeat(scheduler) -> None:
    """Register the daily job and the shortly-after-startup fire."""
    scheduler.add_job(
        send_install_heartbeat,
        heartbeat_trigger(),
        id='install_heartbeat',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        send_install_heartbeat,
        DateTrigger(
            run_date=datetime.now(ZoneInfo(get_server_timezone()))
            + timedelta(seconds=STARTUP_DELAY_S)
        ),
        id='install_heartbeat_startup',
        replace_existing=True,
        max_instances=1,
    )
    logger.info(
        'Install-heartbeat jobs registered (daily at 04:17, once %ds '
        'after startup)',
        STARTUP_DELAY_S,
    )


def heartbeat_trigger() -> CronTrigger:
    """Daily at 04:17 server-local."""
    return CronTrigger(
        hour=4, minute=17, timezone=ZoneInfo(get_server_timezone())
    )


async def send_install_heartbeat() -> None:
    now = datetime.now(UTC)
    day_ago = (now - timedelta(hours=24)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()
    now_iso = now.isoformat()
    try:
        async with async_session() as db:
            store_count = (
                await db.execute(select(func.count(Store.id)))
            ).scalar() or 0
            schedule_count = (
                await db.execute(
                    select(func.count(Schedule.id)).where(
                        Schedule.is_active.is_(True)
                    )
                )
            ).scalar() or 0
            user_count = (
                await db.execute(select(func.count(User.id)))
            ).scalar() or 0
            email_count = (
                await db.execute(select(func.count(EmailAccount.id)))
            ).scalar() or 0
            email_send_disabled_count = (
                await db.execute(
                    select(func.count(EmailAccount.id)).where(
                        EmailAccount.allow_send.is_(False)
                    )
                )
            ).scalar() or 0
            chat_bot_count = (
                await db.execute(select(func.count(ChatBot.id)))
            ).scalar() or 0
            tasks_last_24h = (
                await db.execute(
                    select(func.count(Task.id)).where(
                        Task.created_at >= day_ago
                    )
                )
            ).scalar() or 0
            schedules_fired_7d = (
                await db.execute(
                    select(func.count(func.distinct(Task.schedule_id)))
                    .where(Task.schedule_id.is_not(None))
                    .where(Task.source == 'schedule')
                    .where(Task.is_plan_only.is_not(True))
                    .where(Task.created_at >= week_ago)
                )
            ).scalar() or 0
            store_backend_counts: dict[str, int] = {}
            store_platform_counts: dict[str, int] = {}
            ads_api_bound_store_count = 0
            for backend, platforms, ads_authorized in await db.execute(
                select(
                    Store.browser_backend,
                    Store.platforms,
                    Store.ads_authorized,
                )
            ):
                store_backend_counts[backend] = (
                    store_backend_counts.get(backend, 0) + 1
                )
                try:
                    platform_list = json.loads(platforms or '[]')
                except ValueError:
                    platform_list = []
                for platform in platform_list:
                    store_platform_counts[platform] = (
                        store_platform_counts.get(platform, 0) + 1
                    )
                if ads_authorized:
                    ads_api_bound_store_count += 1
            default_kinds = set()
            default_profile_ids = []
            for (default_profile_id,) in await db.execute(
                select(User.default_profile_id)
            ):
                default_kinds.add(profile_kind_for_id(default_profile_id))
                default_profile_ids.append(default_profile_id)
            chat_bot_platforms = sorted(
                (
                    row[0]
                    for row in await db.execute(
                        select(ChatBot.platform).distinct()
                    )
                ),
            )
            api_token_count = (
                await db.execute(
                    select(func.count(ApiToken.id))
                    .where(ApiToken.revoked_at.is_(None))
                    .where(
                        (ApiToken.expires_at.is_(None))
                        | (ApiToken.expires_at > now_iso)
                    )
                )
            ).scalar() or 0
            gws_enabled = await db.get(AppSettings, 'google_workspace_enabled')
            gws_on = bool(gws_enabled and gws_enabled.value == 'true')
        sync_failed_jobs = await sync_scheduler.failed_jobs()
    except Exception:
        logger.warning('install_heartbeat counts failed', exc_info=True)
        return
    payload = {
        'store_count': store_count,
        'active_schedule_count': schedule_count,
        'user_count': user_count,
        'ai_profile_count': _ai_profile_count(),
        'email_account_count': email_count,
        'chat_bot_count': chat_bot_count,
        'tasks_last_24h': tasks_last_24h,
        'ads_calls_last_24h': api_calls_last_24h(),
        'ai_provider_kinds': sorted({
            profile_kind(p) for p in ProfileManager.list_profiles()
        }),
        'default_ai_provider_kinds': sorted(default_kinds),
        'ai_provider_hosts': provider_hosts(),
        'default_ai_provider_hosts': default_provider_hosts(
            default_profile_ids
        ),
        'store_backend_counts': store_backend_counts,
        'store_platform_counts': store_platform_counts,
        'ads_api_bound_store_count': ads_api_bound_store_count,
        'gws_enabled': gws_on,
        'ticktick_enabled': _ticktick_enabled(),
        'vision_key_set': get_kie_api_key() is not None,
        'chat_bot_platforms': chat_bot_platforms,
        'api_token_count': api_token_count,
        'mcp_calls_last_24h': mcp_calls_last_24h(),
        'ai_custom_providers': custom_provider_entries(),
        'schedules_fired_7d': schedules_fired_7d,
        'email_send_disabled_count': email_send_disabled_count,
        'sync_failed_jobs': sync_failed_jobs,
    }
    telemetry.send(TelemetryEvent.INSTALL_HEARTBEAT, payload)
    telemetry.set_person_properties({
        **payload,
        'app_version': telemetry.APP_VERSION,
        'release_channel': channel_of(telemetry.APP_VERSION),
    })


def _ai_profile_count() -> int:
    """Best-effort profile count (profiles live in a file)."""
    try:
        return len(ProfileManager.list_profiles())
    except Exception:
        return 0


def _ticktick_enabled() -> bool:
    """TickTick/Dida365 is connected when an access token is stored."""
    try:
        return bool(load_backend_config('dida365').get('access_token_enc'))
    except Exception:
        return False
