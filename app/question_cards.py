"""When each task's question cards were last answered (in process).

A question card (AskUserQuestion) is the agent's own multiple choice: a
click — or the first option taken by a hurried user — is not the user
saying, in their own words, which change to make. The ad ``execute``
declaration reads this to tell a reply the user wrote from one a card
supplied (observed: "go ahead and apply it", ambiguous, then a card whose
first option was a bid raise the user never named — and it was applied).

In memory: after a restart nothing is recorded, and an execution is
judged on the user's messages alone, as before this existed.
"""

from datetime import UTC, datetime

_answered: dict[str, str] = {}


def note(task_id: str) -> None:
    """A question card on *task_id* was just answered."""
    _answered[task_id] = datetime.now(UTC).isoformat()


def answered_after(task_id: str, when: str | None) -> bool:
    """Whether a card on *task_id* was answered after ISO time *when*."""
    last = _answered.get(task_id)
    if last is None:
        return False
    if not when:
        return True
    try:
        since = datetime.fromisoformat(when)
    except ValueError:
        return True
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return datetime.fromisoformat(last) > since


def forget(task_id: str) -> None:
    _answered.pop(task_id, None)
