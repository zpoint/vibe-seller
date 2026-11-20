"""Questions nobody answers: park the task, and say where it waits.

Two facts live here, each with one owner:

* **The WAITING write for a question that timed out.** The session
  never writes status: when ``wait_for_answer`` gives up it records a
  ``QuestionPark`` and stops itself (``app.ai.question_wait``). Whichever
  orchestrator owns the session — the auto finalizer, the planned or the
  woken executor — calls ``settle_question_park`` once the session has
  ended and before any error branch (a stopped CLI exits non-zero, and
  that exit is lifecycle, not failure).
* **"Is a question pending on this task?"** — ``pending_question_for``
  answers it for the REST route, the MCP tools and the SPA alike:
  live in the session, being parked right now, or parked on the row.
"""

from datetime import UTC, datetime
import json
import logging

from app.ai.claude_backend_manager import agent_manager
from app.ai.claude_backend_utils import get_next_seq
from app.events.bus import event_bus
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.question_answers import expand_free_text_answers
from app.task_runner import park_waiting_for_pending_question
from app.task_states import TaskStatus
from app.telemetry_tasks import send_question_parked, send_task_resumed

logger = logging.getLogger(__name__)

_PARKABLE = (TaskStatus.RUNNING, TaskStatus.DESIGNING)


def parked_reason(waited_s: float) -> str:
    """The WAITING card text (rendered verbatim by the SPA)."""
    if waited_s < 90:
        waited = f'{round(waited_s)} s'
    else:
        waited = f'{round(waited_s / 60)} min'
    return (
        'The agent asked a question and paused after waiting '
        f'{waited} for an answer. Answering resumes the task.'
    )


async def settle_question_park(db, task: Task | None, session) -> bool:
    """RUNNING/DESIGNING -> WAITING for a session that ended to park.

    True = this call parked the task and the caller must return
    without touching its status. ``park.settled`` is set on every
    exit, so an answer waiting on it (``wait_question_park_settled``)
    never hangs.
    """
    park = getattr(session, '_question_park', None)
    if park is None:
        return False
    try:
        if task is None:
            return False
        if agent_manager.get_session(task.id) is not session:
            # A retry or a profile switch replaced this session; the
            # new one owns the task now.
            return False
        if task.status not in _PARKABLE:
            # Stopped (FAILED) or otherwise moved on while the agent
            # was stopping — the user's decision stands.
            return False
        await park_waiting_for_pending_question(
            task,
            db,
            task.id,
            {
                park.request_id: {
                    'request_id': park.request_id,
                    'questions': park.questions,
                }
            },
            reason=parked_reason(park.waited_s),
            extra={
                'parked_reason': park.reason,
                'parked_at': datetime.now(UTC).isoformat(),
                'asked_at': park.asked_at.isoformat(),
                'waited_s': round(park.waited_s, 1),
            },
        )
        logger.info(
            'Task %s parked in WAITING on question %s after %.0fs',
            task.id[:8],
            park.request_id,
            park.waited_s,
        )
        await event_bus.emit(
            'task_question_parked',
            {
                'task_id': task.id,
                'request_id': park.request_id,
                'questions': park.questions,
            },
        )
        await send_question_parked(task.id, park.asked_at)
        return True
    finally:
        park.settled.set()


def stored_pending_question(task: Task | None) -> dict | None:
    """The pending question parked on a task row, if any."""
    if not task or not task.wait_condition:
        return None
    try:
        condition = json.loads(task.wait_condition)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(condition, dict):
        return None
    pending = condition.get('pending_question')
    if isinstance(pending, dict) and pending.get('request_id'):
        return pending
    return None


def _parked_unanswered(task: Task | None) -> dict | None:
    if task is None or task.status != TaskStatus.WAITING:
        return None
    pending = stored_pending_question(task)
    if pending is None:
        return None
    try:
        condition = json.loads(task.wait_condition)
    except (json.JSONDecodeError, TypeError):
        return None
    if condition.get('answers'):
        # Answered; the resume is on its way.
        return None
    return pending


def pending_question_for(task_id: str, task: Task | None) -> dict | None:
    """The question the task waits on — ONE source for every surface.

    1. live: the session is waiting in its AskUserQuestion handler, or
       its wait just timed out and the agent is stopping (still
       answerable: the answer route waits for the park to settle);
    2. parked: the row is WAITING with an unanswered question — it
       survives the process and a server restart.

    Returns ``{'request_id', 'questions', 'source'}`` or None.
    """
    live = agent_manager.get_pending_questions(task_id)
    if live:
        return {
            'request_id': live.get('request_id'),
            'questions': live.get('questions', []),
            'source': 'live',
        }
    parked = _parked_unanswered(task)
    if parked is not None:
        return {
            'request_id': parked.get('request_id'),
            'questions': parked.get('questions', []),
            'source': 'parked',
        }
    return None


async def pending_payload(db, task_id: str) -> dict:
    """``GET /questions/pending``'s body."""
    data = pending_question_for(task_id, await db.get(Task, task_id))
    if not data:
        return {'pending': False}
    return {'pending': True, **data}


async def answer_parked_question(db, task_id: str, request_id: str):
    """Resolve an answer the live session did not take: the task (as
    loaded AFTER any in-flight park settled) and its parked condition,
    or ``(task, None)`` when there is no matching parked question."""
    await agent_manager.wait_question_park_settled(task_id, request_id)
    task = await db.get(Task, task_id)
    if task is not None:
        await db.refresh(task)
    pending = _parked_unanswered(task)
    if pending is None or pending.get('request_id') != request_id:
        return task, None
    return task, json.loads(task.wait_condition)


async def record_answer_bubble(
    db, task_id: str, questions: list[dict], answers: dict
) -> None:
    """Show the answer in the transcript as the user's own turn (the
    resumed launch persists no second opening prompt). Added to the
    caller's session; it commits."""
    content = answered_lines(questions, answers)
    db.add(
        TaskMessage(
            task_id=task_id,
            role='user',
            content=content,
            seq=await get_next_seq(db, task_id),
        )
    )
    await event_bus.emit(
        'task_message',
        {'task_id': task_id, 'role': 'user', 'content': content},
    )
    task = await db.get(Task, task_id)
    if task is not None:
        send_task_resumed(
            task,
            wait_secs=_parked_for(task),
            wake_reason='question_answered',
        )


def _parked_for(task: Task) -> float | None:
    try:
        parked_at = json.loads(task.wait_condition).get('parked_at')
        return (
            datetime.now(UTC) - datetime.fromisoformat(parked_at)
        ).total_seconds()
    except (AttributeError, TypeError, ValueError):
        return None


def answered_lines(questions: list[dict], answers: dict) -> str:
    """The transcript bubble for an answer given to a parked question."""
    answers = expand_free_text_answers(answers, questions)
    lines = []
    for q in questions:
        text = q.get('question', '') if isinstance(q, dict) else str(q)
        lines.append(f'{text} → {answers.get(text, "")}')
    return '\n'.join(lines)
