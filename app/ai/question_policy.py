"""A run nobody is watching never waits on a human.

A scheduled run fires on its own, at night, with no one at the screen:
a question it asks can only be answered hours later, if at all. Before
this rule an unattended email-triage run asked whether to update a
shared skill, waited 31 hours holding an agent slot, and the scheduled
syncs behind it missed two days. ``VIBE_QUESTION_ANSWER_TIMEOUT_S``
bounds every question; this decides that a scheduled run's question is
not asked at all — the hook declines it at once, and the agent decides
for itself and lists what it would have asked in its result.

The plan-authoring run of a schedule (``is_plan_only``) is exempt: the
user has just created the schedule and is reviewing its plan, so a
question there has someone to answer it.
"""

import logging
from typing import Literal

from app.ai.claude_backend_utils import parse_wait_condition
from app.database import async_session
from app.models.task import Task

logger = logging.getLogger(__name__)

QuestionPolicy = Literal['ask', 'decline']

UNATTENDED_QUESTION_DENY = (
    'Nobody is watching this run: it was started by a schedule, so a '
    'question cannot be answered. Do not ask again. Decide yourself — '
    'take the option you would recommend; when unsure take the safest one '
    '(the one that changes nothing, spends nothing and can be undone); if '
    'no option is safe without a person, skip that step. Finish the rest '
    'of the task. In your final result, add an "Open questions" section '
    'listing each question you would have asked, the option you took, and '
    'why. Do not end the run waiting for a reply either (no wait-condition '
    'for a person): finish it with that result.'
)


def policy_for(task: Task | None) -> QuestionPolicy:
    """``decline`` for a scheduled fire, ``ask`` for everything else."""
    if task is None:
        return 'ask'
    if task.schedule_id and not task.is_plan_only:
        return 'decline'
    return 'ask'


async def question_policy(task_id: str) -> QuestionPolicy:
    """The policy for *task_id*'s row. Any read failure asks — the
    answer timeout still bounds that wait."""
    try:
        async with async_session() as db:
            return policy_for(await db.get(Task, task_id))
    except Exception:
        return 'ask'


def may_wait_on_a_person(task: Task | None) -> bool:
    """Whether *task* may end in WAITING for a person's reply.

    AskUserQuestion is one way to wait on a person; the others park
    the finished run in WAITING with ``check_strategy: manual`` — the
    agent signalling a wait-condition, incomplete todos, a prose
    question. Observed: a scheduled run whose question was declined
    then signalled ``wait-condition`` "waiting for your choice" and
    parked anyway. A scheduled fire completes with its result instead;
    its open questions are in that result.
    """
    return policy_for(task) == 'ask'


def waits_on_a_person(condition: dict | None) -> bool:
    """A wait only a person's reply ends (not an email or an event)."""
    return bool(condition) and (
        condition.get('check_strategy', 'manual') == 'manual'
    )


def agent_wait_condition(task: Task | None, text: str) -> dict | None:
    """The wait-condition the agent signalled in *text*, if it may wait.

    A scheduled fire's wait for a person is dropped — the run completes
    — while its waits on outside events (email, a case reply) stand.
    """
    condition = parse_wait_condition(text or '')
    if waits_on_a_person(condition) and not may_wait_on_a_person(task):
        logger.info(
            'Task %s: scheduled run signalled a wait for a person — '
            'completing instead (nobody to reply)',
            task.id[:8],
        )
        return None
    return condition
