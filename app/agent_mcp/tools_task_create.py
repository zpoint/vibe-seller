"""The ``create_task`` tool, split out of ``tools_tasks.py`` when
attachments pushed that module past the repo's 800-line limit. The
contract is documented in ``tools.py``; what is special to this tool
— the attachment flow — is documented inline below.
"""

from app.agent_mcp.files import as_upload_file, decode_upload
from app.agent_mcp.registry import schema, tool
from app.agent_mcp.support import (
    McpContext,
    ToolError,
    call_route,
    derive_title,
    guidance_for,
    require,
)
from app.models.task import Task
from app.routers import attachments as attachments_route, tasks as tasks_route
from app.schemas.task import TaskCreate


@tool(
    'create_task',
    'Create a task and start it. The task runs the same way it would '
    'if a person created it in the UI: the description is the '
    'instruction to the agent, and the agent may ask questions, '
    'produce a plan, or both. Omit profile_id to use the account '
    "owner's default AI profile.\n\n"
    "TO PLAN OR NOT: omit plan_mode to use the owner's default "
    '(as the web UI does) — pass plan_mode: false to run straight '
    'through (pending → running → completed, stopping only if the '
    'agent asks a question), and plan_mode: true to plan first and '
    'wait for review_plan. A task with NO store ALWAYS plans. '
    'Plan first for anything that changes a store (ads, prices, '
    'listings, money); run directly for read-only or reporting work.',
    schema(
        {
            'description': {
                'type': 'string',
                'description': (
                    'What the agent should do — the task instruction. '
                    'Write it as if to a colleague: no mechanics, no '
                    'tool names.'
                ),
            },
            'title': {
                'type': ['string', 'null'],
                'description': (
                    'A short label for the task list. Optional — one is '
                    'derived from the description when omitted.'
                ),
            },
            'store_id': {
                'type': ['string', 'null'],
                'description': (
                    'The store to act on. Omit for a store-less task '
                    '(those always plan first).'
                ),
            },
            'plan_mode': {
                'type': ['boolean', 'null'],
                'description': (
                    'true to plan first and wait for review_plan; false '
                    "to run straight through. Omit to use the owner's "
                    'default, as the web UI does. A task with no store '
                    'always plans.'
                ),
            },
            'profile_id': {
                'type': ['string', 'null'],
                'description': (
                    'AI profile to run under. Omit to use the account '
                    "owner's default profile."
                ),
            },
            'platform': {'type': ['string', 'null']},
            'country': {'type': ['string', 'null']},
            'attachments': {
                'type': 'array',
                'description': (
                    'Files for the agent to work on (a product image, '
                    'a PDF): {filename, mime_type, content_base64}, '
                    'each up to 10 MB. They land in the task workspace '
                    'BEFORE the agent starts, exactly like an upload '
                    'in the web UI create dialog. Allowed types: png, '
                    'jpeg, gif, webp, pdf.'
                ),
                'items': {
                    'type': 'object',
                    'properties': {
                        'filename': {'type': 'string'},
                        'mime_type': {'type': 'string'},
                        'content_base64': {'type': 'string'},
                    },
                    'required': [
                        'filename',
                        'mime_type',
                        'content_base64',
                    ],
                    'additionalProperties': False,
                },
            },
        },
        required=['description'],
    ),
)
async def create_task(ctx: McpContext, args: dict) -> dict:
    description = require(args, 'description')
    title = args.get('title') or derive_title(description)
    raw_attachments = args.get('attachments') or []
    if not isinstance(raw_attachments, list):
        raise ToolError('attachments must be an array')
    # Mirror the UI's create-with-files flow (frontend
    # submitCreateTask.ts): the task is created DEFERRED, the files
    # land in its workspace, and only then does it start — so the
    # agent's first prompt already sees them.
    uploads = [decode_upload(item) for item in raw_attachments]
    data = TaskCreate(
        title=title,
        description=description,
        store_id=args.get('store_id') or None,
        plan_mode=args.get('plan_mode'),
        profile_id=args.get('profile_id') or None,
        platform=args.get('platform') or None,
        country=args.get('country') or None,
        defer_start=bool(uploads),
    )
    task = await call_route(
        tasks_route.create_task, data, db=ctx.db, current_user=ctx.user
    )
    attached = []
    try:
        for filename, mime_type, payload in uploads:
            record = await call_route(
                attachments_route.upload_attachment,
                task.id,
                as_upload_file(filename, mime_type, payload),
                db=ctx.db,
                _user=ctx.user,
            )
            attached.append({
                'id': record['id'],
                'file_name': record['file_name'],
                'file_size': record['file_size'],
            })
        if uploads:
            # The resume half of the deferred create: the same route the
            # UI's "upload then start" chain ends on.
            await call_route(
                tasks_route.start_task, task.id, db=ctx.db, _user=ctx.user
            )
    except Exception as exc:
        # A deferred task whose upload or start failed stays PENDING
        # forever — no agent is attached, no error column is set, no
        # SSE event fires (the UI routes this through onStartError for
        # exactly that reason; an agent has no screen). Delete what was
        # just created and report, so the caller can retry instead of
        # following a task that will never run.
        try:
            await call_route(
                tasks_route.delete_task,
                task.id,
                db=ctx.db,
                _user=ctx.user,
            )
        except Exception:
            raise ToolError(
                f'Attachments upload/start failed ({exc}) and cleaning '
                f'up the created task {task.id} failed too — it is '
                'PENDING and will never start; stop/delete it before '
                'retrying.'
            ) from exc
        raise ToolError(
            f'Attachments upload/start failed ({exc}) — the task was '
            'deleted again; nothing half-created was left behind. '
            'Retry the create.'
        ) from exc
    fresh = await ctx.db.get(Task, task.id)
    return {
        **await guidance_for(ctx, fresh or task),
        'task_id': task.id,
        'status': (fresh or task).status,
        'store_id': task.store_id,
        'plan_mode': task.plan_mode,
        'ai_profile_id': task.ai_profile_id,
        'attachments': attached,
    }
