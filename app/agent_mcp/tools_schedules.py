"""The schedule tools: list, create, update (edit / pause / replan),
delete, run. One module per tool family, split of the toolset
documented in ``tools.py``; every adapter calls the REST handlers in
``app/routers/schedules.py`` so the plan lifecycle
(``app/plan_states.py``), the fire gate and the permission checks are
the SAME code the web UI runs.

Every result carries the schedule's plan lifecycle in plain words —
``plan_status``, its meaning, ``fires`` and ``next_actions`` from
``app/agent_mcp/lifecycle.py``. A review schedule without an
approved plan never fires: an external agent that reports
"scheduled" for one that cannot fire is exactly the failure those
fields exist to prevent. The approve / request-changes decision and
``get_attention`` live in ``tools_schedule_review.py``.
"""

from app.agent_mcp.lifecycle import plan_guidance
from app.agent_mcp.registry import schema, tool
from app.agent_mcp.support import (
    MAX_TEXT_CHARS,
    McpContext,
    ToolError,
    call_route,
    require,
    trim,
)
from app.models.task import Task
from app.routers import schedules as schedules_route
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate
from app.task_states import TaskStatus

# Fields update_schedule forwards to the PUT route, mapped from the
# tool's arguments. ``paused`` and ``replan`` are NOT here — they map
# to the /pause, /resume and /replan routes (and ScheduleUpdate
# rejects unknown fields outright).
_EDIT_FIELDS = (
    'title',
    'description',
    'schedule_type',
    'schedule_time',
    'schedule_day',
    'interval_value',
    'timezone',
    'ai_profile_id',
    'fanout_serial',
    'finalize_description',
    'plan_version',
)
# For these, an explicit null is a real edit (clear the finalize
# step, unpin the profile, turn fanout_serial off); for the rest a
# null can only mean "leave alone" and is dropped.
_PRESENCE_FIELDS = frozenset({
    'description',
    'schedule_day',
    'timezone',
    'ai_profile_id',
    'finalize_description',
    'fanout_serial',
})


def _edit_fields(args: dict) -> dict:
    """The PUT body fields actually present in the tool arguments."""
    fields: dict = {}
    for key in _EDIT_FIELDS:
        if key in _PRESENCE_FIELDS:
            if key in args:
                fields[key] = args[key]
        elif args.get(key) is not None:
            fields[key] = args[key]
    return fields


_AUTO_NOTE = (
    'AUTO schedule: every fire runs the prompt above directly, on its '
    'cron (next_run) — no plan, no review. Tell your user when it '
    'first runs. run_schedule fires it once now if they want to see it '
    'work; update_schedule(paused=true) stops it.'
)
_REVIEW_NOTE = (
    'REVIEW schedule: a plan-only task is writing the plan now '
    '(plan_task_id — follow it with wait_task). It then WAITS FOR '
    'APPROVAL (plan_status=pending_approval) — the server never '
    'approves on its own and the wait never expires. Show the plan '
    "(get_attention, or this schedule's proposed_plan) to your user "
    'and call review_schedule_plan with their decision. Nothing runs '
    'until a plan is approved.'
)
_PAUSED_NOTE = (
    ' It was created PAUSED (you asked): update_schedule(paused=false) arms it.'
)


async def _planner_terminal(ctx: McpContext, schedule) -> bool:
    """Whether the planning already ended under a `planning` row.

    The plan reaper reconciles a dead planner's schedule row within
    minutes; in that window the honest guidance is "replan", not
    "wait for the planner" — this flag is how the lifecycle table
    tells the two apart (see ``plan_guidance``).
    """
    if str(schedule.plan_status or '') != 'planning':
        return False
    task_id = schedule.current_planning_task_id
    if not task_id:
        # `planning` with no planner pointer: nothing is in flight
        # (creation sets both atomically), so nothing will freeze.
        return True
    task = await ctx.db.get(Task, task_id)
    return bool(
        task and task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}
    )


async def _plan_fields(ctx: McpContext, schedule) -> dict:
    """The lifecycle block every schedule result carries."""
    guidance = plan_guidance(
        schedule, planner_terminal=await _planner_terminal(ctx, schedule)
    )
    return {
        'plan_status': guidance['plan_status'],
        'plan_status_meaning': guidance['plan_status_meaning'],
        'fires': guidance['fires'],
        'next_actions': guidance['next_actions'],
    }


async def schedule_summary(ctx: McpContext, schedule) -> dict:
    """An agent-sized view of one schedule.

    The PLAN TEXTS are part of it, not a summary of them: on a review
    schedule the plan is the whole point of the human checkpoint — the
    agent has to show the proposal to its user before approving it.
    So the approved ``plan`` and the ``proposed_plan`` travel with
    every schedule result, and when one did not fit, the result says
    where the full text is.
    """
    description, _ = trim(schedule.description)
    plan, plan_cut = trim(schedule.plan, MAX_TEXT_CHARS)
    proposed, proposed_cut = trim(schedule.proposed_plan, MAX_TEXT_CHARS)
    return {
        'id': schedule.id,
        'title': schedule.title,
        'description': description,
        'store_id': schedule.store_id,
        'paused': not schedule.is_active,
        'is_system': schedule.is_system,
        'schedule_type': schedule.schedule_type,
        'schedule_time': schedule.schedule_time,
        'schedule_day': schedule.schedule_day,
        'interval_value': schedule.interval_value,
        'timezone': schedule.timezone,
        'next_run': schedule.next_run,
        # True = plan review on (a human approves the plan first);
        # False = auto (each fire runs the prompt).
        'review_plan': bool(schedule.plan_mode) and not schedule.is_system,
        'plan_version': schedule.plan_version,
        'plan_error': schedule.plan_error,
        'plan_task_id': schedule.current_planning_task_id,
        # The APPROVED plan fires run, verbatim (trimmed only if it
        # is longer than one field's budget).
        'plan': plan,
        'plan_truncated': plan_cut,
        # A plan awaiting approval (pending_approval), and the
        # owner's last requested changes.
        'proposed_plan': proposed,
        'proposed_plan_truncated': proposed_cut,
        'plan_feedback': schedule.plan_feedback,
        'plan_full_text_where': (
            f"the schedule's plan panel in the web UI, or "
            f'GET /api/schedules/{schedule.id}/plan'
            if plan_cut or proposed_cut
            else None
        ),
        'last_run_status': schedule.last_run_status,
        'child_task_count': schedule.child_task_count,
        **(await _plan_fields(ctx, schedule)),
    }


async def _create_paused_or_delete(ctx: McpContext, schedule):
    """Pause the just-created schedule — or delete it if that fails.

    The caller asked for a paused schedule. If the pause call fails and
    the schedule were left ACTIVE, it would fire on its cron against
    the caller's explicit wish. Deleting the row we just created
    restores the pre-call world; the agent retries the create.
    """
    try:
        return await call_route(
            schedules_route.pause_schedule,
            schedule_id=schedule.id,
            db=ctx.db,
            _user=ctx.user,
        )
    except Exception as exc:
        try:
            await call_route(
                schedules_route.delete_schedule,
                schedule_id=schedule.id,
                db=ctx.db,
                _user=ctx.user,
            )
        except Exception:
            raise ToolError(
                f'Created schedule {schedule.id} but pausing it failed '
                f'({exc}), and deleting it to undo the create failed '
                'too — pause or delete that schedule NOW.'
            ) from exc
        raise ToolError(
            f'Created schedule {schedule.id} but pausing it failed '
            f'({exc}) — the schedule was deleted again; nothing was '
            'left armed. Retry the create.'
        ) from exc


@tool(
    'list_schedules',
    'List the schedules configured on this server: when each fires '
    '(cron fields + next_run), whether it is paused, whether it is '
    'auto or reviews a plan first (review_plan), its plan status and '
    'what to do next.',
    schema({
        'store_id': {
            'type': ['string', 'null'],
            'description': 'Only schedules for this store.',
        },
    }),
)
async def list_schedules(ctx: McpContext, args: dict) -> dict:
    schedules = await call_route(
        schedules_route.list_schedules,
        store_id=args.get('store_id') or None,
        db=ctx.db,
        _user=ctx.user,
    )
    return {'schedules': [await schedule_summary(ctx, s) for s in schedules]}


@tool(
    'create_schedule',
    'Create a scheduled task. By default it is AUTO and ACTIVE: every '
    'fire runs your prompt (description) directly on its cron — the '
    'same as creating it in the web UI. Pass review_plan=true when '
    'your user wants to read and approve a plan before anything runs: '
    'a plan-only task writes the plan, the schedule waits for approval '
    '(no time limit), and you show the plan to your user and call '
    'review_schedule_plan. Pass paused=true only to set it up without '
    'arming it. '
    "Omit ai_profile_id to inherit the owner's default AI profile at "
    'fire time.',
    schema(
        {
            'title': {
                'type': 'string',
                'description': 'Short label for the schedule.',
            },
            'description': {
                'type': ['string', 'null'],
                'description': (
                    'The prompt every fire runs — the instruction to '
                    'the task agent, as if to a colleague.'
                ),
            },
            'schedule_type': {
                'type': 'string',
                'enum': ['minutes', 'hours', 'days', 'weekly', 'monthly'],
                'description': 'The recurrence unit.',
            },
            'schedule_time': {
                'type': 'string',
                'description': (
                    "'HH:MM' (or 'HH:MM:SS') fire time — required for "
                    'days/weekly/monthly, ignored for minutes/hours. '
                    "Default '09:00'."
                ),
            },
            'schedule_day': {
                'type': ['integer', 'null'],
                'description': (
                    'Day of week (1=Mon..7=Sun) for weekly, day of '
                    'month for monthly; null otherwise.'
                ),
            },
            'interval_value': {
                'type': 'integer',
                'minimum': 1,
                'description': 'Every N units (e.g. every 2 days).',
            },
            'timezone': {
                'type': ['string', 'null'],
                'description': (
                    'IANA zone for schedule_time. Omit for the '
                    "server's default."
                ),
            },
            'store_id': {
                'type': ['string', 'null'],
                'description': (
                    'Bind to one store. Omit for an all-stores '
                    'schedule (one task per store per fire).'
                ),
            },
            'phase_mode': {
                'type': ['string', 'null'],
                'enum': ['single', 'fanout', None],
                'description': (
                    'All-stores schedules only: single (one no-store '
                    'task per fire) or fanout (one task per store). '
                    "Omit for the server's default."
                ),
            },
            'fanout_serial': {
                'type': ['boolean', 'null'],
                'description': (
                    "Fanout only: run the batch's stores one at a "
                    'time instead of all at once.'
                ),
            },
            'finalize_description': {
                'type': ['string', 'null'],
                'description': (
                    'Fanout only: after every store of a batch '
                    'finishes, run one reduce task with this prompt.'
                ),
            },
            'ai_profile_id': {
                'type': ['string', 'null'],
                'description': (
                    "Pin an AI profile. Omit to inherit the owner's "
                    'current default at fire time.'
                ),
            },
            'review_plan': {
                'type': ['boolean', 'null'],
                'description': (
                    'true: the agent writes a plan first and the '
                    'schedule runs nothing until a human approves it '
                    '(review_schedule_plan). Omit/false: AUTO — each '
                    'fire runs the prompt directly.'
                ),
            },
            'paused': {
                'type': ['boolean', 'null'],
                'description': (
                    'true creates it PAUSED (nothing fires until '
                    'update_schedule(paused=false)). Omit to create '
                    'it active, like the web UI.'
                ),
            },
        },
        required=['title', 'schedule_type'],
    ),
)
async def create_schedule(ctx: McpContext, args: dict) -> dict:
    data = ScheduleCreate(
        title=require(args, 'title'),
        description=args.get('description') or None,
        store_id=args.get('store_id') or None,
        schedule_type=require(args, 'schedule_type'),
        schedule_time=args.get('schedule_time') or '09:00',
        schedule_day=args.get('schedule_day'),
        interval_value=args.get('interval_value') or 1,
        timezone=args.get('timezone'),
        phase_mode=args.get('phase_mode'),
        fanout_serial=bool(args.get('fanout_serial')),
        finalize_description=args.get('finalize_description') or None,
        ai_profile_id=args.get('ai_profile_id'),
        plan_mode=bool(args.get('review_plan')),
    )
    schedule = await call_route(
        schedules_route.create_schedule,
        data=data,
        db=ctx.db,
        current_user=ctx.user,
    )
    # ACTIVE unless the caller asked for paused (same as the UI). An
    # auto schedule runs exactly the prompt the caller just sent; a
    # review schedule cannot fire anything unapproved — the checkpoint
    # is the approval, not a pause.
    paused = args.get('paused') is True
    if paused:
        schedule = await _create_paused_or_delete(ctx, schedule)
    note = _REVIEW_NOTE if schedule.plan_mode else _AUTO_NOTE
    return {
        **(await schedule_summary(ctx, schedule)),
        'schedule_id': schedule.id,
        'note': note + (_PAUSED_NOTE if paused else ''),
    }


@tool(
    'update_schedule',
    'Edit a schedule, switch plan review on/off, pause/resume it, or '
    're-plan it — one call can do several: the edit applies first, '
    'then replan, then pause/resume (a refusal from a later step '
    'leaves earlier steps applied). On an AUTO schedule, an edited '
    'prompt simply runs from the next fire. On a REVIEW schedule, '
    'editing the prompt starts a new plan at once, and nothing runs '
    'until that plan is approved (review_schedule_plan). '
    'review_plan=true turns review on (same effect); false turns it '
    'off (the plan is dropped; fires run the prompt). replan spawns '
    'a fresh plan-only task (follow it with wait_task).',
    schema(
        {
            'schedule_id': {
                'type': 'string',
                'description': 'The schedule id.',
            },
            'title': {'type': 'string'},
            'description': {
                'type': ['string', 'null'],
                'description': (
                    'The prompt every fire runs. On a review schedule '
                    'a change starts a new plan for approval.'
                ),
            },
            'review_plan': {
                'type': ['boolean', 'null'],
                'description': (
                    'true: switch plan review on (a plan is written '
                    'and must be approved before it runs again). '
                    'false: switch to AUTO (fires run the prompt).'
                ),
            },
            'schedule_type': {
                'type': 'string',
                'enum': ['minutes', 'hours', 'days', 'weekly', 'monthly'],
            },
            'schedule_time': {'type': 'string'},
            'schedule_day': {'type': ['integer', 'null']},
            'interval_value': {'type': 'integer', 'minimum': 1},
            'timezone': {'type': ['string', 'null']},
            'ai_profile_id': {
                'type': ['string', 'null'],
                'description': 'Pin an AI profile; omit to keep.',
            },
            'fanout_serial': {'type': ['boolean', 'null']},
            'finalize_description': {'type': ['string', 'null']},
            'plan_version': {
                'type': ['integer', 'null'],
                'description': (
                    'Optimistic lock: the plan_version you last saw. '
                    'The server refuses (412) if it moved.'
                ),
            },
            'paused': {
                'type': ['boolean', 'null'],
                'description': (
                    'true pauses the schedule; false resumes it (a '
                    'review schedule needs an approved plan).'
                ),
            },
            'replan': {
                'type': ['boolean', 'null'],
                'description': (
                    'true spawns a fresh plan-only task to write a '
                    'new plan (after a failure); it then waits for '
                    'approval.'
                ),
            },
        },
        required=['schedule_id'],
    ),
)
async def update_schedule(ctx: McpContext, args: dict) -> dict:
    schedule_id = require(args, 'schedule_id')
    fields = _edit_fields(args)
    if args.get('review_plan') is not None:
        fields['plan_mode'] = bool(args['review_plan'])
    paused = args.get('paused')
    wants_replan = bool(args.get('replan'))
    if not fields and paused is None and not wants_replan:
        raise ToolError(
            'nothing to update — pass fields to edit, review_plan, '
            'paused, or replan'
        )

    if fields:
        await call_route(
            schedules_route.update_schedule,
            schedule_id=schedule_id,
            data=ScheduleUpdate(**fields),
            db=ctx.db,
            current_user=ctx.user,
        )
    if wants_replan:
        await call_route(
            schedules_route.replan_schedule,
            schedule_id=schedule_id,
            db=ctx.db,
            current_user=ctx.user,
        )
    if paused is True:
        await call_route(
            schedules_route.pause_schedule,
            schedule_id=schedule_id,
            db=ctx.db,
            _user=ctx.user,
        )
    elif paused is False:
        await call_route(
            schedules_route.resume_schedule,
            schedule_id=schedule_id,
            db=ctx.db,
            _user=ctx.user,
        )

    final = await call_route(
        schedules_route.get_schedule,
        schedule_id=schedule_id,
        db=ctx.db,
        _user=ctx.user,
    )
    return {
        **(await schedule_summary(ctx, final)),
        'updated_fields': sorted(fields),
        'replanned': wants_replan,
    }


@tool(
    'delete_schedule',
    'Delete a schedule (its fired tasks are kept). Its future fires '
    'stop; the plan and cron definition are gone.',
    schema(
        {'schedule_id': {'type': 'string', 'description': 'The schedule id.'}},
        required=['schedule_id'],
    ),
)
async def delete_schedule(ctx: McpContext, args: dict) -> dict:
    schedule_id = require(args, 'schedule_id')
    await call_route(
        schedules_route.delete_schedule,
        schedule_id=schedule_id,
        db=ctx.db,
        _user=ctx.user,
    )
    return {'ok': True, 'schedule_id': schedule_id}


@tool(
    'run_schedule',
    'Fire a schedule now, outside its cron: the same task(s) its '
    'next scheduled fire would create. An auto schedule runs its '
    'prompt; a review schedule runs its APPROVED plan and is refused '
    'without one (the refusal says why). NOTE: this fires a PAUSED '
    "schedule too (the web UI's Run-now button is not disabled by the "
    "pause) — get your user's go-ahead before firing a paused one.",
    schema(
        {'schedule_id': {'type': 'string', 'description': 'The schedule id.'}},
        required=['schedule_id'],
    ),
)
async def run_schedule(ctx: McpContext, args: dict) -> dict:
    schedule_id = require(args, 'schedule_id')
    result = await call_route(
        schedules_route.trigger_schedule,
        schedule_id=schedule_id,
        db=ctx.db,
        current_user=ctx.user,
    )
    # All-stores schedules fan out in the background: the route
    # answers {'ok', 'mode'} with no single task id. Store-bound
    # schedules answer {'ok', 'task_id', 'status'}.
    task_id = (result or {}).get('task_id')
    mode = (result or {}).get('mode') or 'store'
    final = await call_route(
        schedules_route.get_schedule,
        schedule_id=schedule_id,
        db=ctx.db,
        _user=ctx.user,
    )
    if task_id is None and mode == 'single':
        note = (
            'One task (not tied to a store) is being created in the '
            'background; find it with list_tasks.'
        )
    elif task_id is None:
        note = (
            'One task per active store is being created; find them '
            'with list_tasks.'
        )
    elif final.plan_mode:
        note = 'The task runs the approved plan (it does not re-plan).'
    else:
        note = 'The task runs the schedule prompt (auto).'
    return {
        'ok': True,
        'schedule_id': schedule_id,
        'task_id': task_id,
        'mode': mode,
        'note': note,
        **(await _plan_fields(ctx, final)),
    }
