"""What a chat turn has to tell the agent beyond the message itself.

``POST /messages`` delivers the user's message as the prompt, replacing
the ``bundle.prompt`` that ``_format_header`` builds from the task's own
title and description. That is right for a follow-up — by then the task
is already in the resumed CLI session or in the prior conversation — but
it means everything else the turn needs (the draft plan, prior history,
and on the first turn the task itself) has to be assembled here.

Extracted from ``app.routers.tasks_conversation`` so the message handler
reads as routing rather than prompt-assembly.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.compaction import build_history_prompt, dump_history_file
from app.models.task import Task
from app.models.task_message import TaskMessage

_REVISED_PLAN_INSTRUCTION = (
    'IMPORTANT: Your final output MUST be the '
    'COMPLETE revised plan — not just the changes '
    'or a summary. Include all original sections '
    '(updated as needed) so the plan can fully '
    'replace the previous version. '
    'If the user asks questions, use '
    'AskUserQuestion to ask them interactively, '
    'then revise the plan after receiving answers.'
)


async def build_chat_context(
    task: Task,
    db: AsyncSession,
    *,
    exclude_message_id: str,
    is_plan_feedback: bool,
    has_resumable_session: bool,
) -> str:
    """Assemble the ``extra_context`` for one chat turn.

    ``exclude_message_id`` is the message being delivered right now — it
    is already the prompt, so it must not also appear as history.
    """
    # When resuming, skip conversation reconstruction — the CLI
    # session already has all prior context. Only inject mode
    # switch instructions and the plan feedback instruction.
    if has_resumable_session:
        return f'\n\n{_REVISED_PLAN_INSTRUCTION}' if is_plan_feedback else ''

    # No resumable session — build full context from DB
    result = await db.execute(
        select(TaskMessage)
        .where(TaskMessage.task_id == task.id)
        .order_by(TaskMessage.seq)
    )
    messages = result.scalars().all()

    # Always dump history file so the agent can read
    # prior conversation on demand (even when a plan
    # is shown inline).
    prior_msgs = [
        m
        for m in messages
        if m.role in ('user', 'assistant') and m.id != exclude_message_id
    ]
    history_file = None
    history_dicts: list[dict] = []
    if prior_msgs:
        history_dicts = [
            {'role': m.role, 'content': m.content, 'seq': m.seq}
            for m in prior_msgs
        ]
        history_file = dump_history_file(task.id, history_dicts)

    if is_plan_feedback and task.plan:
        context = (
            '\n\n## Current Plan (draft, not yet confirmed)\n'
            'The following plan was designed in a previous '
            'session. The user is providing feedback to '
            'revise or extend it.\n\n'
            f'{_REVISED_PLAN_INSTRUCTION}\n\n' + task.plan
        )
    elif task.plan:
        context = '\n\n## Task Plan\n' + task.plan
    elif not prior_msgs and task.description:
        # FIRST turn of a task that has never run. Every other entry
        # point delivers the task's own description via bundle.prompt
        # (_format_header); this one replaces that prompt with the chat
        # message, and normally the description is already in the
        # resumed session or the prior conversation. With neither — a
        # PENDING task the user messages before it has ever started —
        # the description reaches the agent NOWHERE: nothing in
        # system_extra carries it. Observed live on a task carrying a
        # description plus image attachments: the agent got a bare
        # "please start", read the images, found no instructions, and
        # stopped to ask the user what the task was, with the answer
        # sitting unread in its own ``description`` column.
        context = f'\n\n## Task\n{task.title}\n\n{task.description}'
    elif prior_msgs and history_file:
        context = '\n\n## Prior Conversation\n' + build_history_prompt(
            history_dicts, history_file
        )
    else:
        context = ''

    # Add history file reference to system prompt when
    # a plan was shown inline (agent can read full
    # conversation from the file if needed).
    if history_file and task.plan:
        context += (
            f'\n\nNote: Full conversation history '
            f'({len(prior_msgs)} messages) is saved '
            f'at {history_file}. Read it if you need '
            f'context from prior discussion.'
        )
    return context
