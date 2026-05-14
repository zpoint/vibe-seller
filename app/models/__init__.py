from app.models.app_settings import AppSettings
from app.models.browser_session import BrowserSession
from app.models.email_account import EmailAccount
from app.models.event import Event
from app.models.event_activity import EventActivity
from app.models.schedule import Schedule
from app.models.schedule_state import ScheduleState
from app.models.screenshot import Screenshot
from app.models.store import Store
from app.models.store_email_link import StoreEmailLink
from app.models.task import Task
from app.models.task_attachment import TaskAttachment
from app.models.task_log import TaskLog
from app.models.task_message import TaskMessage
from app.models.task_step import TaskStep
from app.models.user import User
from app.models.wecom_bot import WeComBot
from app.models.ziniao_account import ZiniaoAccount

_all_models = [
    AppSettings,
    User,
    Store,
    BrowserSession,
    Schedule,
    ScheduleState,
    Task,
    TaskStep,
    Screenshot,
    TaskLog,
    TaskAttachment,
    TaskMessage,
    ZiniaoAccount,
    Event,
    EventActivity,
    EmailAccount,
    StoreEmailLink,
    WeComBot,
]
