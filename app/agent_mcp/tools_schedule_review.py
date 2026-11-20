"""What waits for a human, and the schedule plan decision.

Two tools, split from ``tools_schedules.py`` (size limit):

* ``get_attention`` — one call answers "is anything waiting for my
  user?": schedule plans awaiting approval (with the full proposed
  plan), task questions, and standalone plans awaiting review. Each
  item carries the exact next call.
* ``review_schedule_plan`` — approve a review schedule's plan, or send
  it back with the user's requested changes. Calls the same handlers
  as the web UI's Plan panel (``app/routers/schedule_review.py``).

A blank agent connected only over MCP can therefore run the whole
review loop: create_schedule(review_plan=true) → wait_task on the
planner → get_attention → show the plan → review_schedule_plan.
"""

from sqlalchemy import select

from app.agent_mcp.lifecycle import (
    ANSWER_QUESTION,
    GET_PENDING_QUESTIONS,
    REVIEW_PLAN,
    REVIEW_SCHEDULE_PLAN,
    schedule_fired,
)
from app.agent_mcp.registry import schema, tool
from app.agent_mcp.support import (
    McpContext,
    ToolError,
    call_route,
    require,
    trim,
)
from app.agent_mcp.tools_schedules import schedule_summary
from app.models.schedule import Schedule
from app.models.task import Task
from app.plan_states import PlanStatus
from app.question_park import pending_question_for
from app.routers import (
    schedule_review as review_route,
    schedules as schedules_route,
)
from app.schemas.schedule import PlanApproveRequest, PlanChangesRequest
from app.task_states import TaskStatus

# Bound the listing: this is a to-do list for a person, not a dump.
_MAX_ITEMS = 20

# Statuses a task can hold an open question in.
_QUESTION_STATUSES = (
    TaskStatus.DESIGNING,
    TaskStatus.RUNNING,
    TaskStatus.WAITING,
)


def _has_question(task: Task) -> bool:
    # One source with the pending-question route: live, being parked,
    # or parked on the row (and not yet answered).
    return pending_question_for(task.id, task) is not None


@tool(
    'get_attention',
    'Everything waiting for a human right now, with the exact next '
    'call for each: schedule plans awaiting approval (the full '
    'proposed plan is included — show it to your user), questions '
    'agents asked, and task plans awaiting review. Nothing here '
    'expires: a plan awaiting approval waits until someone decides. '
    'Call it when your user asks "anything for me?", after creating '
    'a schedule with review_plan=true, or before reporting that '
    'nothing is pending.',
    schema({}),
)
async def get_attention(ctx: McpContext, args: dict) -> dict:
    approvals = []
    rows = (
        await ctx.db.execute(
            select(Schedule)
            .where(
                Schedule.plan_status == PlanStatus.PENDING_APPROVAL.value,
                Schedule.plan_mode.is_(True),
                Schedule.is_system.is_not(True),
            )
            .order_by(Schedule.updated_at)
            .limit(_MAX_ITEMS)
        )
    ).scalars()
    for sched in rows:
        plan, cut = trim(sched.proposed_plan)
        approvals.append({
            'schedule_id': sched.id,
            'title': sched.title,
            'store_id': sched.store_id,
            'plan_version': sched.plan_version,
            'proposed_plan': plan,
            'proposed_plan_truncated': cut,
            'next': (
                f'Show the plan to your user. Then {REVIEW_SCHEDULE_PLAN}('
                f"schedule_id='{sched.id}', approve=true, "
                f'plan_version={sched.plan_version}) — or approve=false '
                "with feedback='<what to change>'."
            ),
        })

    questions = []
    plans = []
    tasks = (
        await ctx.db.execute(
            select(Task)
            .where(Task.status.in_([*_QUESTION_STATUSES, TaskStatus.PLANNED]))
            .order_by(Task.updated_at)
        )
    ).scalars()
    for task in tasks:
        if task.status in _QUESTION_STATUSES and _has_question(task):
            if len(questions) < _MAX_ITEMS:
                questions.append({
                    'task_id': task.id,
                    'title': task.title,
                    'status': str(task.status),
                    'next': (
                        f"{GET_PENDING_QUESTIONS}(task_id='{task.id}'), "
                        f'then {ANSWER_QUESTION}.'
                    ),
                })
        elif (
            task.status == TaskStatus.PLANNED
            and task.plan
            and not task.is_plan_only
            and not schedule_fired(task)
            and len(plans) < _MAX_ITEMS
        ):
            plans.append({
                'task_id': task.id,
                'title': task.title,
                'next': (
                    f'Show the plan (get_task), then {REVIEW_PLAN}(task_id='
                    f"'{task.id}', approve=true|false)."
                ),
            })

    total = len(approvals) + len(questions) + len(plans)
    return {
        'needs_attention': total > 0,
        'schedule_plan_approvals': approvals,
        'task_questions': questions,
        'task_plan_reviews': plans,
        'note': (
            'Nothing is waiting for a human.'
            if total == 0
            else 'Each item says the next call. Approvals and answers '
            'never expire — but nothing moves until someone decides.'
        ),
    }


@tool(
    'review_schedule_plan',
    "Decide on a review schedule's plan (plan_status "
    'pending_approval): approve=true makes it the plan every fire '
    'runs; approve=false with feedback sends it back — a new planning '
    'run revises it and it comes back to pending_approval. A PLAN IS '
    "YOUR USER'S CHECKPOINT: show them the plan and approve only "
    'with their go-ahead (unless they told you to approve without '
    'asking). Pass the plan_version you showed them; if the plan '
    'changed since, the call is refused and you re-read it. Feedback '
    'also works on a ready plan (revise it; the approved one keeps '
    'running meanwhile).',
    schema(
        {
            'schedule_id': {'type': 'string', 'description': 'The schedule.'},
            'approve': {
                'type': 'boolean',
                'description': 'true approves; false requests changes.',
            },
            'feedback': {
                'type': ['string', 'null'],
                'description': (
                    'Required with approve=false: what should change, '
                    "in your user's words."
                ),
            },
            'plan_version': {
                'type': ['integer', 'null'],
                'description': 'The plan_version of the plan you showed.',
            },
        },
        required=['schedule_id', 'approve'],
    ),
)
async def review_schedule_plan(ctx: McpContext, args: dict) -> dict:
    schedule_id = require(args, 'schedule_id')
    approve = args.get('approve')
    if not isinstance(approve, bool):
        raise ToolError('approve (true/false) is required')
    version = args.get('plan_version')
    if approve:
        await call_route(
            review_route.approve_plan,
            schedule_id=schedule_id,
            data=PlanApproveRequest(plan_version=version),
            db=ctx.db,
            _user=ctx.user,
        )
        note = (
            'Approved. The schedule now fires this plan on its cron'
            ' (unless paused); run_schedule fires it once now.'
        )
    else:
        feedback = (args.get('feedback') or '').strip()
        if not feedback:
            raise ToolError(
                'feedback is required with approve=false — say what '
                'should change.'
            )
        await call_route(
            review_route.request_plan_changes,
            schedule_id=schedule_id,
            data=PlanChangesRequest(feedback=feedback, plan_version=version),
            db=ctx.db,
            current_user=ctx.user,
        )
        note = (
            'Sent back. A new planning run is revising the plan '
            '(wait_task on plan_task_id); it returns to '
            'pending_approval for another decision.'
        )
    final = await call_route(
        schedules_route.get_schedule,
        schedule_id=schedule_id,
        db=ctx.db,
        _user=ctx.user,
    )
    return {
        **(await schedule_summary(ctx, final)),
        'decision': 'approved' if approve else 'changes_requested',
        'note': note,
    }
