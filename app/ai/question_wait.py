"""The bounded wait behind AskUserQuestion.

**An agent slot is held only by an agent that can make progress.** A
question waits on a person, and the agent does nothing while it waits
— but it still holds its slot, its process and its browser. Unbounded,
one unanswered question on an unattended run held a slot for more than
a day and starved every other scheduled task behind it.

So the wait is bounded: ``wait_for_answer`` returns ``False`` once
``VIBE_QUESTION_ANSWER_TIMEOUT_S`` passes, the session records a
``QuestionPark`` and stops itself, and the orchestrator that owns the
session parks the task in WAITING (``app.question_park``). Answering
later resumes it.

Pure asyncio, no DB: the real session and the workflow FakeAgent use
the same functions, so the tests exercise the decision the product
makes.
"""

import asyncio
import dataclasses
from datetime import datetime
import enum

from app.env_options import Options


class Verdict(enum.StrEnum):
    ANSWERED = 'answered'
    TIMED_OUT = 'timed_out'
    STOPPED = 'stopped'


@dataclasses.dataclass
class QuestionPark:
    """A question that timed out: the session is stopping itself and
    the orchestrator still has to write WAITING.

    ``settled`` is set by ``settle_question_park`` on every exit
    (parked, superseded, task gone), so an answer that arrives while
    the agent is stopping can wait for the row instead of being lost.
    """

    request_id: str
    questions: list[dict]
    asked_at: datetime
    waited_s: float
    reason: str = 'answer_timeout'
    settled: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)


def answer_timeout_s() -> float:
    """Read at call time so a test or a restarted server can move it."""
    return Options.QUESTION_ANSWER_TIMEOUT_S.get_float()


async def wait_for_answer(event: asyncio.Event, timeout_s: float) -> bool:
    """Wait for ``event``; True = it was set, False = timed out.

    ``timeout_s <= 0`` waits forever. There is no separate timer to
    cancel: the bound lives in this await, so an answer or a stop ends
    it by setting the event and nothing can fire later.
    """
    if timeout_s <= 0:
        await event.wait()
        return True
    try:
        await asyncio.wait_for(event.wait(), timeout_s)
    except TimeoutError:
        return event.is_set()
    return True


def verdict(*, answered: bool, timed_out: bool, stopping: bool) -> Verdict:
    """Decide what a finished wait means.

    An answer that is present wins — even when the timeout fired in
    the same tick, the person did answer and the agent gets it. A stop
    comes next: the user (or a supersession) ended the run, so nothing
    is parked. Only an unanswered, unstopped wait parks.
    """
    if answered:
        return Verdict.ANSWERED
    if stopping or not timed_out:
        return Verdict.STOPPED
    return Verdict.TIMED_OUT
