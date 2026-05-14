"""Event-name constants for telemetry.send() callers.

Hardcoded strings drift; one typo in a router and the dashboard loses
a dimension silently. Every emit site references these constants so
renames are atomic and a grep for "EventName" finds every emit.
"""


class TelemetryEvent:
    """All vibe-seller telemetry event names."""

    APP_STARTED = 'app_started'
    DAILY_HEARTBEAT = 'daily_heartbeat'

    STORE_CREATED = 'store_created'
    STORE_DELETED = 'store_deleted'

    TASK_CREATED = 'task_created'
    TASK_COMPLETED = 'task_completed'
    TASK_FAILED = 'task_failed'
    TASK_RESUMED = 'task_resumed'

    SCHEDULE_FIRED = 'schedule_fired'

    SETTING_CHANGED = 'setting_changed'
    USER_PREF_CHANGED = 'user_pref_changed'

    AI_PROFILE_CREATED = 'ai_profile_created'
    AI_PROFILE_DELETED = 'ai_profile_deleted'
    AI_PROFILE_DEFAULT_SET = 'ai_profile_default_set'

    EMAIL_ACCOUNT_ADDED = 'email_account_added'
    EMAIL_ACCOUNT_REMOVED = 'email_account_removed'

    ZINIAO_ACCOUNT_ADDED = 'ziniao_account_added'
    ZINIAO_ACCOUNT_REMOVED = 'ziniao_account_removed'

    INTEGRATION_GWS_ENABLED = 'integration_gws_enabled'
    INTEGRATION_GWS_DISABLED = 'integration_gws_disabled'

    ADMIN_USER_CREATED = 'admin_user_created'
    ADMIN_USER_DELETED = 'admin_user_deleted'

    BROWSER_SESSION_ATTEMPTED = 'browser_session_attempted'
    BROWSER_SESSION_STARTED = 'browser_session_started'
    BROWSER_SESSION_FAILED = 'browser_session_failed'


class BrowserFailureReason:
    """Reasons a browser-session attempt gave up — keeps cardinality
    bounded. Used as ``error_category`` on
    ``TelemetryEvent.BROWSER_SESSION_FAILED``.
    """

    WSL_NOT_RUNNING = 'wsl_not_running'
    WSL_WRONG_PORT = 'wsl_wrong_port'
    NORMAL_MODE = 'normal_mode'
    WRONG_PORT = 'wrong_port'
    LAUNCH_FAILED = 'launch_failed'
    STARTUP_TIMEOUT = 'startup_timeout'


class TaskFailurePhase:
    """Where in the task lifecycle a failure happened."""

    PRE_RUNNING = 'pre_running'
    RUNNING = 'running'
    PIPELINE = 'pipeline'


class TaskFailureCategory:
    """Coarse-bucket fallback when ``task.error_category`` isn't set."""

    ZINIAO_UNAVAILABLE = 'ziniao_unavailable'
    NO_RESULT = 'no_result'
    AGENT_SET_ERROR = 'agent_set_error'
    CLI_ERROR = 'cli_error'
    UNHANDLED_EXCEPTION = 'unhandled_exception'
