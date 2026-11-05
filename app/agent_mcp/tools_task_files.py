"""The task-adjacent operator tools: the debug log, the output files,
and the image-generation confirm gate.

Split out of ``tools.py`` alongside the other tool families; the
contract they follow is documented there. Three tools:

* ``get_task_log`` — the full step log (paginated) plus the
  conversation, for debugging a run the summaries cannot explain.
* ``task_files`` — the files a task produced: list them, read one
  (inline text, or a download URL / base64 blob), or get the ZIP URL.
* ``image_request`` — see and resolve the confirm card a task agent's
  image-generation proposal is parked on.
"""

import os
from urllib.parse import quote

from app.agent_mcp.files import (
    absolute_url,
    file_result,
    validated_task_path,
)
from app.agent_mcp.registry import schema, tool
from app.agent_mcp.support import (
    MAX_TEXT_CHARS,
    McpContext,
    ToolError,
    call_route,
    require,
    trim,
)
from app.routers import (
    tasks as tasks_route,
    tasks_conversation as conversation_route,
    tasks_files as tasks_files_route,
    vision as vision_route,
)

# get_task_log pagination: the default page and its ceiling. A debug
# log exists to be paged through, not dumped.
DEFAULT_LOG_LIMIT = 100
MAX_LOG_LIMIT = 500
# How many conversation messages ride along with a log page. The most
# recent ones are what explains the run's tail; the full transcript is
# what get_task_log pages through with offset on the STEPS.
DEFAULT_MESSAGE_LIMIT = 50
MAX_MESSAGE_LIMIT = 200

# Fields a step carries in the debug log. ``description`` and
# ``result`` are the payload an operator actually debugs with, so they
# are included (trimmed per step) — the summary in get_task omits them.
_STEP_TEXT_FIELDS = {
    'description': MAX_TEXT_CHARS,
    'result': MAX_TEXT_CHARS,
    'error': MAX_TEXT_CHARS,
}


def _step_entry(step) -> dict:
    entry = {
        'step_index': step.step_index,
        'name': step.name,
        'action_type': step.action_type,
        'status': step.status,
        'started_at': step.started_at,
        'completed_at': step.completed_at,
    }
    for field, limit in _STEP_TEXT_FIELDS.items():
        value, _ = trim(getattr(step, field, None), limit)
        if value is not None:
            entry[field] = value
    return entry


@tool(
    'get_task_log',
    'The debug log for one task: the FULL step log (paginated with '
    'offset/limit) plus the conversation messages. Use when get_task '
    'is not enough — a run that failed mid-way, or a result you cannot '
    'explain from the summary. Steps are ordered; the messages are the '
    'most recent ones.',
    schema(
        {
            'task_id': {'type': 'string', 'description': 'The task id.'},
            'offset': {
                'type': 'integer',
                'minimum': 0,
                'description': ('Skip this many steps (default 0).'),
            },
            'limit': {
                'type': 'integer',
                'minimum': 1,
                'maximum': MAX_LOG_LIMIT,
                'description': (
                    'How many steps to return '
                    f'(default {DEFAULT_LOG_LIMIT}, max {MAX_LOG_LIMIT}).'
                ),
            },
            'messages_limit': {
                'type': 'integer',
                'minimum': 1,
                'maximum': MAX_MESSAGE_LIMIT,
                'description': (
                    'How many recent conversation messages to include '
                    f'(default {DEFAULT_MESSAGE_LIMIT}, '
                    f'max {MAX_MESSAGE_LIMIT}).'
                ),
            },
        },
        required=['task_id'],
    ),
)
async def get_task_log(ctx: McpContext, args: dict) -> dict:
    task_id = require(args, 'task_id')
    offset = max(0, int(args.get('offset') or 0))
    limit = args.get('limit') or DEFAULT_LOG_LIMIT
    limit = max(1, min(int(limit), MAX_LOG_LIMIT))
    messages_limit = args.get('messages_limit') or DEFAULT_MESSAGE_LIMIT
    messages_limit = max(1, min(int(messages_limit), MAX_MESSAGE_LIMIT))

    # The task row itself is read through the same route get_task uses,
    # so a missing task answers the same way here.
    task = await call_route(
        tasks_route.get_task, task_id, db=ctx.db, _user=ctx.user
    )
    steps = await call_route(
        tasks_route.get_task_steps, task_id, db=ctx.db, _user=ctx.user
    )
    messages = await call_route(
        conversation_route.get_task_messages, task_id, db=ctx.db, _user=ctx.user
    )

    page = steps[offset : offset + limit]
    trimmed_messages = messages[-messages_limit:]
    return {
        'task_id': task.id,
        'title': task.title,
        'status': task.status,
        'error': task.error,
        'steps': [_step_entry(step) for step in page],
        'steps_total': len(steps),
        'steps_offset': offset,
        'steps_truncated': offset + len(page) < len(steps),
        'messages': [
            {
                'id': m['id'],
                'role': m['role'],
                'content': trim(m['content'], MAX_TEXT_CHARS)[0],
                'created_at': m['created_at'],
            }
            for m in trimmed_messages
        ],
        'messages_total': len(messages),
        'messages_truncated': len(messages) > len(trimmed_messages),
    }


@tool(
    'task_files',
    'The files a task produced in its workspace. action=list names '
    'them; action=read returns one (text inline, anything else as a '
    'download URL — small binaries also ride along as base64); '
    'action=zip_url returns a URL that downloads everything as one '
    'ZIP (a vst_ bearer works on it).',
    schema(
        {
            'task_id': {'type': 'string', 'description': 'The task id.'},
            'action': {
                'type': 'string',
                'enum': ['list', 'read', 'zip_url'],
                'description': 'What to do.',
            },
            'path': {
                'type': ['string', 'null'],
                'description': (
                    'The file to read, relative to the task workspace '
                    '(required for action=read).'
                ),
            },
        },
        required=['task_id', 'action'],
    ),
)
async def task_files(ctx: McpContext, args: dict) -> dict:
    task_id = require(args, 'task_id')
    action = require(args, 'action')

    if action == 'list':
        files = await call_route(
            tasks_files_route.list_task_files, task_id, _user=ctx.user
        )
        return {'task_id': task_id, 'files': files}

    if action == 'read':
        path = validated_task_path(args.get('path'))
        response = await call_route(
            tasks_files_route.download_task_file,
            task_id,
            path,
            _user=ctx.user,
        )
        return file_result(
            ctx,
            path=path,
            abs_path=response.path,
            mime_type=response.media_type,
            # Percent-encoded like the workspace branch: a filename
            # with '?' or '#' would otherwise truncate the URL at the
            # first delimiter. '/' stays a separator (quote's default
            # safe set).
            download_path=f'/api/tasks/{task_id}/files/{quote(path)}',
        )

    if action == 'zip_url':
        # The route builds (and validates the task dir for) the exact
        # archive the URL serves. Called directly, its cleanup
        # BackgroundTask never runs (nothing sends the response), so
        # the temp file is removed here — the URL re-builds it on
        # demand, exactly as the UI's download link does.
        response = await call_route(
            tasks_files_route.download_task_files_zip,
            task_id,
            _user=ctx.user,
        )
        tmp = getattr(response, 'path', None)
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return {
            'task_id': task_id,
            'url': absolute_url(ctx, f'/api/tasks/{task_id}/files-zip'),
        }

    raise ToolError(
        f'Unknown action {action!r} — accepted: list, read, zip_url'
    )


# The card fields an operator needs to decide. The full catalog is
# dropped from the compact block (wait_task carries it in trimmed
# form); image_request(action=get) returns the whole card. The card's
# own optional label (``kind``: "main"/"infographic"/…) is renamed
# ``image_kind`` here so the pending block's ``kind`` can stay the
# DISCRIMINATOR (``image_confirm``).
_IMAGE_CARD_FIELDS = (
    'request_id',
    'prompt',
    'model',
    'reference_images',
    'output_name',
)


def compact_image_card(card: dict) -> dict:
    """The pending-card fields wait_task reports (models → ids only)."""
    compact = {field: card.get(field) for field in _IMAGE_CARD_FIELDS}
    compact['image_kind'] = card.get('kind')
    models = card.get('models') or []
    compact['models'] = [m.get('id') for m in models if m.get('id')]
    return compact


@tool(
    'image_request',
    'See and resolve the image-generation confirm card a task agent is '
    'parked on: the agent proposed an image (prompt, model) and the '
    'server is WAITING for a human decision. action=get shows the '
    'card; action=confirm approves it (optionally with an edited '
    'prompt/model — your user may change either); action=cancel '
    'refuses it. A card is a checkpoint: show it to your user unless '
    'they already told you to proceed.',
    schema(
        {
            'task_id': {'type': 'string', 'description': 'The task id.'},
            'action': {
                'type': 'string',
                'enum': ['get', 'confirm', 'cancel'],
                'description': 'What to do with the card.',
            },
            'request_id': {
                'type': ['string', 'null'],
                'description': (
                    'From action=get. Omit to use the card the task is '
                    'holding right now.'
                ),
            },
            'prompt': {
                'type': ['string', 'null'],
                'description': (
                    'confirm only: an edited prompt (your user may '
                    'change what was proposed).'
                ),
            },
            'model': {
                'type': ['string', 'null'],
                'description': (
                    'confirm only: an edited model id (one of the '
                    "card's models)."
                ),
            },
        },
        required=['task_id', 'action'],
    ),
)
async def image_request(ctx: McpContext, args: dict) -> dict:
    task_id = require(args, 'task_id')
    action = require(args, 'action')

    pending = await call_route(
        vision_route.get_pending_image_request,
        task_id,
        current_user=ctx.user,
    )

    if action == 'get':
        if not pending.get('pending'):
            return {
                'task_id': task_id,
                'pending': False,
                'note': (
                    'No image request is waiting. The task may still be '
                    'working; wait_task reports one the moment it appears.'
                ),
            }
        return {'task_id': task_id, 'pending': True, **pending}

    if action not in ('confirm', 'cancel'):
        raise ToolError(
            f'Unknown action {action!r} — accepted: get, confirm, cancel'
        )

    request_id = args.get('request_id') or pending.get('request_id')
    if not request_id:
        raise ToolError(
            'No pending image request for this task — call '
            'image_request(action=get) first.'
        )
    body: dict = {'request_id': request_id, 'action': action}
    if action == 'confirm':
        # Only pass the edits the caller actually made: an explicit
        # null must not override the proposed prompt (the route treats
        # a missing key and a null the same, but staying shape-faithful
        # keeps that contract visible here).
        if isinstance(args.get('prompt'), str) and args['prompt'].strip():
            body['prompt'] = args['prompt'].strip()
        if isinstance(args.get('model'), str) and args['model'].strip():
            body['model'] = args['model'].strip()
    await call_route(
        vision_route.confirm_task_image,
        task_id,
        body,
        current_user=ctx.user,
    )
    return {
        'task_id': task_id,
        'ok': True,
        'action': action,
        'request_id': request_id,
        'note': (
            'The decision was delivered; the task agent continues with '
            'it. Follow the task with wait_task.'
        ),
    }
