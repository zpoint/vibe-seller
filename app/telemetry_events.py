"""Event-name constants for telemetry.send() callers.

Hardcoded strings drift; one typo in a router and the dashboard loses
a dimension silently. Every emit site references these constants so
renames are atomic and a grep for "EventName" finds every emit.
"""

import enum


class TelemetryEvent:
    """All vibe-seller telemetry event names."""

    APP_STARTED = 'app_started'
    INSTALL_HEARTBEAT = 'install_heartbeat'

    STORE_CREATED = 'store_created'
    STORE_DELETED = 'store_deleted'

    TASK_CREATED = 'task_created'
    TASK_STARTED = 'task_started'
    TASK_COMPLETED = 'task_completed'
    TASK_FAILED = 'task_failed'
    TASK_RESUMED = 'task_resumed'
    TASK_CANCELLED = 'task_cancelled'
    TASK_RETRIED = 'task_retried'

    AGENT_QUESTION_ASKED = 'agent_question_asked'
    AGENT_QUESTION_ANSWERED = 'agent_question_answered'

    # User-action events (the plan/message/schedule verdicts mirror
    # the frontend event names of the same actions; server rows carry
    # `via`, frontend rows do not).
    TASK_MESSAGE_SUBMITTED = 'task_message_submitted'
    PLAN_APPROVED = 'plan_approved'
    PLAN_CHANGES_REQUESTED = 'plan_changes_requested'

    SCHEDULE_CREATED = 'schedule_created'
    SCHEDULE_UPDATED = 'schedule_updated'
    SCHEDULE_DELETED = 'schedule_deleted'
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

    BROWSER_SESSION_STARTED = 'browser_session_started'
    BROWSER_SESSION_FAILED = 'browser_session_failed'

    CHAT_BOT_ADDED = 'chat_bot_added'
    CHAT_BOT_REMOVED = 'chat_bot_removed'
    IMAGE_GENERATED = 'image_generated'
    WORKSPACE_ASSISTANT_MESSAGE = 'workspace_assistant_message'

    STORE_UPDATED = 'store_updated'
    ADS_STORE_BOUND = 'ads_store_bound'
    ADS_STORE_UNBOUND = 'ads_store_unbound'
    VISION_KEY_SAVED = 'vision_key_saved'
    DIDA365_AUTHORIZED = 'dida365_authorized'
    DIDA365_MCP_SETUP = 'dida365_mcp_setup'

    API_TOKEN_CREATED = 'api_token_created'
    API_TOKEN_REVOKED = 'api_token_revoked'
    MCP_SESSION_STARTED = 'mcp_session_started'
    MCP_TOOL_CALLED = 'mcp_tool_called'

    TASK_RESULT_GATED = 'task_result_gated'
    AD_TASK_DECLARED = 'ad_task_declared'
    KNOWLEDGE_CLEANUP_FINISHED = 'knowledge_cleanup_finished'
    SCHEDULE_PLAN_STATUS_CHANGED = 'schedule_plan_status_changed'
    SCHEDULE_FIRE_SKIPPED = 'schedule_fire_skipped'
    EMAIL_SEND_PERMISSION_CHANGED = 'email_send_permission_changed'
    EMAIL_SENT = 'email_sent'
    IMAGE_REQUEST_RESOLVED = 'image_request_resolved'
    UPGRADE_RESTARTED = 'upgrade_restarted'


class GateOutcome:
    """Outcome of one ``task_result_gated``."""

    REFUSED = 'refused'
    FAILED_OPEN = 'failed_open'
    BANNER_MARKED = 'banner_marked'


class TaskSource(enum.StrEnum):
    """What launched a task; recorded on the row at creation."""

    UI = 'ui'
    SCHEDULE = 'schedule'
    SUBTASK = 'subtask'
    MCP_EXTERNAL = 'mcp_external'
    CHAT_BOT = 'chat_bot'
    WORKSPACE_ASSISTANT = 'workspace_assistant'
    OTHER = 'other'


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


class McpToolOutcome:
    """Outcome of one ``mcp_tool_called``."""

    OK = 'ok'
    ERROR = 'error'
    DENIED = 'denied'


class McpErrorCategory:
    """Bounded failure buckets for ``mcp_tool_called.error_category``."""

    INVALID_PARAMS = 'invalid_params'
    TOOL_ERROR = 'tool_error'
    ROUTE_DENIED = 'route_denied'
    ROUTE_ERROR = 'route_error'
    UNEXPECTED = 'unexpected'


class McpClientName:
    """Known MCP client names; anything else buckets to ``other``."""

    KNOWN = {'claude': 'claude-code', 'cursor': 'cursor', 'codex': 'codex'}
    OTHER = 'other'

    @classmethod
    def bucket(cls, raw: str | None) -> str:
        name = (raw or '').strip().lower()
        for needle, bucket in cls.KNOWN.items():
            if needle in name:
                return bucket
        return cls.OTHER


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
