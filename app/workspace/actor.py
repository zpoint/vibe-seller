"""Who a workspace git commit is made ON BEHALF OF.

Every workspace commit used to be authored "Vibe Seller
<agent@vibe-seller.local>", so ``file_history`` could not say who
changed a knowledge file — and a write made over the hosted MCP
endpoint changes what every future task agent reads. The invariant
this module enforces:

* a commit made while a signed-in caller's request is on the stack is
  authored by that caller, and its message says how they
  authenticated (``Via: session`` / ``api_token`` / ``service`` /
  ``no_auth``);
* a commit made by a task's own agent — the task pipeline's
  "Knowledge update after task" sweeps, the agent subprocess's own
  git — keeps the agent identity (no actor is set there, by
  construction: the pipeline entry points clear it, and the agent
  subprocess builds its own env).

Mechanics: a :class:`contextvars.ContextVar` set by ``get_current_user``
(the one place every authenticated caller is resolved — it already
records ``request.state.auth_via``) and read ONLY by the workspace git
identity builder (``ignored_paths.git_env``) and
``WorkspaceManager._auto_commit`` (for the ``Via:`` trailer). asyncio
copies the context into background tasks at spawn time, which is why
the task-pipeline entry points clear it explicitly — a follow-up
spawned from an authenticated request must not file the AGENT's
knowledge edits under the caller's name.

The var is cleared at the TOP of ``get_current_user`` too, so a
context that already resolved one caller (in-process tests share a
task; production gets one task per request) can never speak for a
previous request.
"""

from contextvars import ContextVar
import re
from typing import NamedTuple

from app.models.user import User

# git forbids <, > and newlines in an author name; an email local part
# gets the conservative subset (spaces are not quotable here).
_NAME_UNSAFE = re.compile(r'[<>\n\r]+')
_EMAIL_UNSAFE = re.compile(r'[^A-Za-z0-9._-]+')
_EMAIL_DOMAIN = 'users.vibe-seller.local'


class WorkspaceActor(NamedTuple):
    """The caller a workspace commit is attributed to."""

    name: str
    email: str
    via: str


_request_actor: ContextVar[WorkspaceActor | None] = ContextVar(
    'workspace_request_actor', default=None
)


def actor_for(user: User, via: str) -> WorkspaceActor:
    """Build the git identity for *user* authenticated *via*.

    The name keeps the username as git accepts it (spaces are legal;
    ``<``, ``>`` and newlines are not — they would let a crafted
    username forge a second author line). The email is synthesized:
    users have no email we may publish into a git history that the
    knowledge sync round-trips to a shared repo.
    """
    raw_name = (user.username or 'user').strip() or 'user'
    name = _NAME_UNSAFE.sub(' ', raw_name).strip() or 'user'
    local = _EMAIL_UNSAFE.sub('_', raw_name).strip('._-').lower() or 'user'
    return WorkspaceActor(name=name, email=f'{local}@{_EMAIL_DOMAIN}', via=via)


def set_request_actor(actor: WorkspaceActor | None) -> None:
    """Attribute workspace commits in this context to *actor*.

    ``None`` means "no caller on the stack" — commits fall back to the
    agent identity.
    """
    _request_actor.set(actor)


def clear_request_actor() -> None:
    """Drop any caller attribution from this context.

    Called at the entry of the task-pipeline orchestrators: from there
    down, the work (and its commits) is the task agent's, whoever
    launched the pipeline.
    """
    _request_actor.set(None)


def current_actor() -> WorkspaceActor | None:
    """The caller this context's workspace commits belong to, if any."""
    return _request_actor.get()
