"""Daily install heartbeat with the install's inventory counts."""

from datetime import UTC, datetime, timedelta
import logging
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select

from app import telemetry
from app.ads_client import api_calls_last_24h
from app.ai.profiles import ProfileManager
from app.database import async_session
from app.models.chat_bot import ChatBot
from app.models.email_account import EmailAccount
from app.models.schedule import Schedule
from app.models.store import Store
from app.models.task import Task
from app.models.user import User
from app.release_channel import channel_of
from app.telemetry_events import TelemetryEvent
from app.utils.timezone import get_server_timezone

logger = logging.getLogger(__name__)


def register_install_heartbeat(scheduler) -> None:
    """Register the daily job."""
    scheduler.add_job(
        send_install_heartbeat,
        heartbeat_trigger(),
        id='install_heartbeat',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info('Install-heartbeat job registered (daily at 04:17)')


def heartbeat_trigger() -> CronTrigger:
    """Daily at 04:17 server-local."""
    return CronTrigger(
        hour=4, minute=17, timezone=ZoneInfo(get_server_timezone())
    )


async def send_install_heartbeat() -> None:
    day_ago = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
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
    except Exception:
        logger.warning('install_heartbeat counts failed', exc_info=True)
        return
    ai_profile_count = _ai_profile_count()
    telemetry.send(
        TelemetryEvent.INSTALL_HEARTBEAT,
        {
            'store_count': store_count,
            'active_schedule_count': schedule_count,
            'user_count': user_count,
            'ai_profile_count': ai_profile_count,
            'email_account_count': email_count,
            'chat_bot_count': chat_bot_count,
            'tasks_last_24h': tasks_last_24h,
            'ads_calls_last_24h': api_calls_last_24h(),
        },
    )
    telemetry.set_person_properties({
        'app_version': telemetry.APP_VERSION,
        'release_channel': channel_of(telemetry.APP_VERSION),
    })


def _ai_profile_count() -> int:
    """Best-effort profile count (profiles live in a file)."""
    try:
        return len(ProfileManager.list_profiles())
    except Exception:
        return 0
