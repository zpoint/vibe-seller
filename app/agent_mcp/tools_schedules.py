"""The schedule tools: list, create, update (edit / pause / replan),
delete, run. One module per tool family, split of the toolset
documented in ``tools.py``; every adapter calls the REST handlers in
``app/routers/schedules.py`` so the plan lifecycle
(``app/plan_states.py``), the fire gate and the permission checks are
the SAME code the web UI runs.

Every result carries the schedule's plan lifecycle in plain words —
``plan_status``, its meaning, ``fires`` and ``next_actions`` from
``app/agent_mcp/lifecycle.py``. A schedule whose plan is not
``ready`` never fires: an external agent that reports
"scheduled" for one that cannot fire is exactly the failure those
fields exist to prevent.
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
from app.routers import schedules as schedules_route
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate

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


_CREATE_NOTE = (
    'The schedule does not fire until its plan is ready. A plan-only '
    'task (plan_task_id) is authoring the plan now: follow it with '
    'wait_task. When it finishes, the plan is frozen onto the '
    'schedule (plan_status=ready) — the planning task executes '
    'nothing, and the server accepts the plan without anyone reading '
    'it. Show the plan (in `plan`) to your user and resume the '
    'schedule with update_schedule(paused=false) only once they have '
    'approved it.'
)


def _plan_fields(schedule) -> dict:
    """The lifecycle block every schedule result carries."""
    guidance = plan_guidance(schedule)
    return {
        'plan_status': guidance['plan_status'],
        'plan_status_meaning': guidance['plan_status_meaning'],
        'fires': guidance['fires'],
        'next_actions': guidance['next_actions'],
    }


def _schedule_summary(schedule) -> dict:
    """An agent-sized view of one schedule.

    The FROZEN PLAN TEXT is part of it, not a summary of it: a
    schedule's plan is the whole point of the human checkpoint — the
    agent has to show it to its user before the schedule runs
    anything. So the plan travels with every schedule result, and
    when it did not fit, the result says where the full text is.
    """
    description, _ = trim(schedule.description)
    plan, plan_cut = trim(schedule.plan, MAX_TEXT_CHARS)
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
        'plan_mode': schedule.plan_mode,
        'plan_version': schedule.plan_version,
        'plan_error': schedule.plan_error,
        'plan_task_id': schedule.current_planning_task_id,
        # The plan the schedule will run, verbatim (trimmed only if
        # it is longer than one field's budget).
        'plan': plan,
        'plan_truncated': plan_cut,
        'plan_full_text_where': (
            f"the schedule's plan panel in the web UI, or "
            f'GET /api/schedules/{schedule.id}/plan'
            if plan_cut
            else None
        ),
        'last_run_status': schedule.last_run_status,
        'child_task_count': schedule.child_task_count,
        **_plan_fields(schedule),
    }


@tool(
    'list_schedules',
    'List the schedules configured on this server: when each fires '
    '(cron fields + next_run), whether it is paused, its plan status '
    '(a schedule whose plan is not ready never fires) and what to do '
    'next.',
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
    return {'schedules': [_schedule_summary(s) for s in schedules]}


@tool(
    'create_schedule',
    'Create a scheduled task. Every schedule plans first: a '
    'plan-only task authors the plan (follow it with wait_task); '
    'once it finishes, the plan is frozen onto the schedule and the '
    'schedule fires on its cron. The schedule does not fire until '
    "the plan is ready. Omit ai_profile_id to inherit the owner's "
    'default AI profile at fire time. '
    'Pass paused=true unless your user already told you to go ahead: '
    "a schedule's plan is accepted by the server without anyone "
    'reading it, so pausing keeps the plan a checkpoint your user '
    'can actually see — you resume it with '
    'update_schedule(paused=false) after they have seen the plan. '
    'The schedule cannot fire while the plan is still being written, '
    'so pausing at creation closes that window completely.',
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
            'paused': {
                'type': ['boolean', 'null'],
                'description': (
                    'true creates the schedule PAUSED, so it cannot '
                    'fire until you resume it with '
                    'update_schedule(paused=false). Pass true unless '
                    'your user already told you to go ahead: the '
                    "schedule's plan is accepted by the server without "
                    'anyone reading it, and pausing keeps that plan a '
                    'checkpoint your user can see.'
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
    )
    schedule = await call_route(
        schedules_route.create_schedule,
        data=data,
        db=ctx.db,
        current_user=ctx.user,
    )
    if args.get('paused'):
        # Pause it in the SAME tool call: the REST create path has no
        # "create it paused" (ScheduleCreate carries no is_active and
        # the route always creates it active), and the plan is still
        # being written — the fire gate refuses a PLANNING schedule —
        # so pausing here leaves no window in which it could fire.
        schedule = await call_route(
            schedules_route.pause_schedule,
            schedule_id=schedule.id,
            db=ctx.db,
            _user=ctx.user,
        )
    return {
        **_schedule_summary(schedule),
        'schedule_id': schedule.id,
        'note': _CREATE_NOTE,
    }


@tool(
    'update_schedule',
    'Edit a schedule, pause/resume it, or re-plan it — one call can '
    'do several: the edit applies first, then replan, then '
    'pause/resume (a refusal from a later step leaves earlier steps '
    'applied). Editing the prompt makes the stored plan stale: the '
    'schedule will not fire again until it is re-planned. Resuming '
    'requires a ready plan. replan spawns a fresh plan-only task '
    '(follow it with wait_task).',
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
                    'The prompt every fire runs. Changing it makes '
                    'the stored plan stale.'
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
                    'true pauses the schedule; false resumes it '
                    '(resume needs a ready plan).'
                ),
            },
            'replan': {
                'type': ['boolean', 'null'],
                'description': (
                    'true spawns a fresh plan-only task to author a '
                    'new plan (replaces a stale or failed one).'
                ),
            },
        },
        required=['schedule_id'],
    ),
)
async def update_schedule(ctx: McpContext, args: dict) -> dict:
    schedule_id = require(args, 'schedule_id')
    fields = _edit_fields(args)
    paused = args.get('paused')
    wants_replan = bool(args.get('replan'))
    if not fields and paused is None and not wants_replan:
        raise ToolError(
            'nothing to update — pass fields to edit, paused, or replan'
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
        **_schedule_summary(final),
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
    'next scheduled fire would create. Requires the plan to be '
    'ready — a schedule whose plan is not ready never fires, and '
    'the refusal says why. The created task does not re-plan: it '
    'runs the frozen plan.',
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
    note = (
        'One task per active store is being created; find them with list_tasks.'
        if task_id is None
        else 'The task runs the frozen plan (it does not re-plan).'
    )
    final = await call_route(
        schedules_route.get_schedule,
        schedule_id=schedule_id,
        db=ctx.db,
        _user=ctx.user,
    )
    return {
        'ok': True,
        'schedule_id': schedule_id,
        'task_id': task_id,
        'mode': mode,
        'note': note,
        **_plan_fields(final),
    }
