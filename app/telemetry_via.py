"""Request-scoped ``via`` channel: HOW a user action reached the
server — derived from the authenticated principal and the request
path, never from a client-supplied field.

Set once by ``app.auth.get_current_user`` (the single point every
authenticated caller resolves through) and read by the telemetry
emitters. Non-request flows leave it unset: the emitters then fall
back to the task's own source (scheduler -> ``schedule``, chat bot
-> ``chat_bot``), and a sub-task is always ``subtask`` regardless of
the channel that created it.
"""

from contextvars import ContextVar
import functools
import inspect

UI = 'ui'
MCP = 'mcp'
API = 'api'
CHAT_BOT = 'chat_bot'
SCHEDULE = 'schedule'
SUBTASK = 'subtask'
OTHER = 'other'

_via: ContextVar[str | None] = ContextVar('telemetry_via', default=None)


def set_via(via: str | None) -> None:
    _via.set(via)


def clear_via() -> None:
    _via.set(None)


def current_via() -> str:
    """The channel of the request on this context, else ``other``."""
    return _via.get() or OTHER


def via_for_request(path: str, auth_via: str) -> str:
    """The ``via`` of one request: the MCP endpoint is ``mcp`` even
    when it authenticates like REST; a ``vst_`` token on plain REST
    is ``api``; the browser (or an auth-off server) is ``ui``."""
    if path.startswith('/api/mcp'):
        return MCP
    return {
        'session': UI,
        'no_auth': UI,
        'api_token': API,
        'service': API,
    }.get(auth_via, OTHER)


def via_for_task(task) -> str:
    """The ``via`` stamp for task-lifecycle events: a sub-task is
    ``subtask`` whatever created it; a request's channel wins
    otherwise; scheduler and chat-bot runs carry no request."""
    if getattr(task, 'parent_task_id', None):
        return SUBTASK
    channel = _via.get()
    if channel:
        return channel
    return {
        'schedule': SCHEDULE,
        'chat_bot': CHAT_BOT,
        'subtask': SUBTASK,
    }.get(getattr(task, 'source', '') or '', OTHER)


# TaskSource values, keyed by the via channel — task provenance is
# derived from WHO is calling (this module), never from a request
# field. Imported here (not the other way round) so the enum stays
# the single vocabulary.
_SOURCE_OF_VIA = {
    UI: 'ui',
    MCP: 'mcp_external',
    API: 'other',
    CHAT_BOT: 'chat_bot',
    SCHEDULE: 'schedule',
    SUBTASK: 'subtask',
    OTHER: 'other',
}


def source_of_via(via: str | None) -> str:
    """The TaskSource of one via channel."""
    return _SOURCE_OF_VIA.get(via or OTHER, 'other')


def unattributed(fn):
    """Wrap a scheduler-job callable so the job runs with no request
    channel.

    apscheduler schedules its callbacks through the event loop, which
    snapshots the REGISTERING context — and schedule jobs are
    registered from inside schedule create/update requests. Without
    this wrapper every fire inherits the registering request's
    channel and reports ``via='ui'`` instead of ``schedule``. Same
    reasoning as ``clear_request_actor`` at the task-pipeline entry
    points: a background run must not act with the caller's identity.
    Applied at REGISTRATION (not inside the job) because the manual
    trigger runs the same job functions from an in-request task and
    SHOULD keep the request's channel.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def _async(*args, **kwargs):
            clear_via()
            return await fn(*args, **kwargs)

        return _async

    @functools.wraps(fn)
    def _sync(*args, **kwargs):
        clear_via()
        return fn(*args, **kwargs)

    return _sync


# A question is ANSWERED over IPC on the agent's own task, long after
# the answering request returned — so the answering channel is handed
# over by request_id (one in-process map; both ends live in the
# server).
_answer_via: dict[str, str] = {}


def note_answer_via(request_id: str) -> None:
    """Record which channel is answering question *request_id*."""
    if request_id:
        _answer_via[request_id] = current_via()


def take_answer_via(request_id: str | None) -> str:
    """The channel that answered *request_id* (``other`` when none)."""
    return _answer_via.pop(request_id or '', '') or OTHER
