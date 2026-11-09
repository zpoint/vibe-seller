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

import asyncio
import os
from urllib.parse import quote

from app import vision_catalog
from app.agent_mcp.files import (
    IMAGES_KEY,
    MAX_IMAGE_BLOCK_BYTES,
    MAX_REPLY_IMAGE_BYTES,
    absolute_url,
    as_upload_file,
    decode_uploads,
    file_result,
    image_block,
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
        # File reads and an image preview's Pillow decode block: keep
        # them off the event loop (SSE streams share it).
        return await asyncio.to_thread(
            file_result,
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


# A confirm card may carry this many references; more is a mistake,
# and every one shown costs an image block in the reply.
MAX_CARD_REFERENCES = 14


def _task_ref_path(task_id: str, ref: str):
    """The on-disk file for a workspace-relative reference, or None.

    Same confinement as the generator (``vision._resolve_reference``):
    resolved, and still inside this task's directory.
    """
    if not isinstance(ref, str) or ref.startswith(('http://', 'https://')):
        return None
    task_dir = (vision_route._TASKS_DIR / task_id).resolve()
    local = (task_dir / ref).resolve()
    if not local.is_relative_to(task_dir) or not local.is_file():
        return None
    return local


def _show_references(task_id: str, refs: list) -> tuple[list, list]:
    """``(reference entries, image blocks)`` for a card's references.

    Workspace files are SHOWN; a remote URL is listed, never fetched by
    the server (that would be an SSRF hop) — the client can open it.
    All blocks share :data:`MAX_REPLY_IMAGE_BYTES`: the client forwards
    this reply to its model, whose request has a ceiling, so once the
    budget runs low images shrink to previews and then are only listed.
    Blocking (file I/O, Pillow) — run it in a thread.
    """
    entries, images = [], []
    budget = MAX_REPLY_IMAGE_BYTES
    for ref in (refs or [])[:MAX_CARD_REFERENCES]:
        entry = {'ref': ref}
        local = _task_ref_path(task_id, ref)
        block = None
        if local is not None:
            block = image_block(
                local, max_bytes=min(MAX_IMAGE_BLOCK_BYTES, budget)
            )
        if block is not None:
            budget -= len(block['data']) * 3 // 4
            images.append(block)
            entry['shown'] = len(images)  # 1-based image number
        elif local is not None:
            entry['not_shown'] = 'over the reply image budget; use task_files'
        elif isinstance(ref, str) and ref.startswith(('http://', 'https://')):
            entry['url'] = ref
        entries.append(entry)
    return entries, images


def _confirm_model(card: dict, requested: str) -> tuple[str, str | None]:
    """The card model to confirm, or a ToolError naming the valid ones.

    The route silently keeps the agent's proposal for an id outside the
    card — fine for a stale browser card, wrong for an operator who
    asked for something specific and would never hear it was ignored.
    A retired id (``gpt-image-2-2k``) maps to its successor, said so.
    """
    ids = [m.get('id') for m in card.get('models') or [] if m.get('id')]
    if requested in ids:
        return requested, None
    mapped = vision_catalog.retired_successor(requested)
    if mapped in ids:
        return mapped, f'{requested} was retired; confirmed {mapped} instead.'
    raise ToolError(
        f'Unknown model {requested!r} for this card. Choose one of: '
        + ', '.join(ids)
    )


async def _collect_references(
    ctx: McpContext, task_id: str, args: dict, existing: int
):
    """References the operator adds on confirm, as the card's own
    "add reference" does: workspace paths, URLs, or uploaded images
    (saved through the SAME upload route the card posts to).

    Everything is validated — count included, against the card's
    ``existing`` references — BEFORE an upload is written, so a refused
    call leaves no orphan files behind."""
    paths = args.get('add_references') or []
    uploads = decode_uploads(args.get('reference_uploads') or [])
    total = existing + len(paths) + len(uploads)
    if total > MAX_CARD_REFERENCES:
        raise ToolError(
            f'A card takes at most {MAX_CARD_REFERENCES} reference images '
            f'in total; this one has {existing} and you added '
            f'{len(paths) + len(uploads)}.'
        )
    for filename, mime_type, _ in uploads:
        if not mime_type.startswith('image/'):
            raise ToolError(
                f'reference_uploads: {filename} is {mime_type}, not an image'
            )
    added: list[str] = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            raise ToolError('add_references entries must be strings')
        ref = raw.strip()
        if ref.startswith(('http://', 'https://')):
            added.append(ref)
            continue
        ref = validated_task_path(ref)
        if _task_ref_path(task_id, ref) is None:
            raise ToolError(
                f'add_references: {ref!r} is not a file in this task — '
                'list them with task_files(action=list)'
            )
        added.append(ref)
    for filename, mime_type, data in uploads:
        saved = await call_route(
            vision_route.upload_reference,
            task_id,
            as_upload_file(filename, mime_type, data),
            current_user=ctx.user,
        )
        added.append(saved['path'])
    return added


@tool(
    'image_request',
    'See and resolve the image-generation confirm card a task agent is '
    'parked on: the agent proposed an image (prompt, model) and the '
    'server is WAITING for a human decision. action=get shows the card '
    '— prompt, the proposed model, every selectable model with its '
    'per-image price, and the reference images themselves (as images). '
    'action=confirm approves it, optionally with an edited prompt, a '
    'different model (one of the card ids), and extra reference images '
    '(task files, URLs, or uploads); action=cancel refuses it. To '
    'answer in words instead (e.g. "make it brighter"), send_message '
    'retires the card and the agent replies. The finished image lands '
    "in the task's generated_images/ — task_files(action=read) shows "
    'it; action=zip_url downloads everything. A card is a checkpoint: '
    'show it to your user unless they already told you to proceed.',
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
                    "confirm only: a model id from the card's models "
                    '(an unknown id is an error, not silently ignored).'
                ),
            },
            'add_references': {
                'type': ['array', 'null'],
                'items': {'type': 'string'},
                'description': (
                    'confirm only: extra reference images — paths of '
                    'files already in the task (task_files list) or '
                    'http(s) URLs.'
                ),
            },
            'reference_uploads': {
                'type': ['array', 'null'],
                'items': {
                    'type': 'object',
                    'properties': {
                        'filename': {'type': 'string'},
                        'mime_type': {'type': 'string'},
                        'content_base64': {'type': 'string'},
                    },
                    'required': ['filename', 'mime_type', 'content_base64'],
                },
                'description': (
                    'confirm only: reference images to upload from your '
                    'side (e.g. a product photo your user gave you).'
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
        entries, images = await asyncio.to_thread(
            _show_references, task_id, pending.get('reference_images') or []
        )
        out = {'task_id': task_id, 'pending': True, **pending}
        out['references'] = entries
        if images:
            out[IMAGES_KEY] = images
        return out

    if action not in ('confirm', 'cancel'):
        raise ToolError(
            f'Unknown action {action!r} — accepted: get, confirm, cancel'
        )

    request_id = args.get('request_id') or pending.get('request_id')
    if not request_id or not pending.get('pending'):
        raise ToolError(
            'No pending image request for this task — call '
            'image_request(action=get) first.'
        )
    if request_id != pending.get('request_id'):
        raise ToolError(
            f'Card {request_id} is no longer pending — the task now holds '
            f'{pending.get("request_id")}; call image_request(action=get).'
        )
    body: dict = {'request_id': request_id, 'action': action}
    notes = []
    if action == 'confirm':
        # Only pass the edits the caller actually made: an explicit
        # null must not override the proposed prompt.
        if isinstance(args.get('prompt'), str) and args['prompt'].strip():
            body['prompt'] = args['prompt'].strip()
        if isinstance(args.get('model'), str) and args['model'].strip():
            body['model'], note = _confirm_model(pending, args['model'].strip())
            if note:
                notes.append(note)
        added = await _collect_references(
            ctx, task_id, args, len(pending.get('reference_images') or [])
        )
        if added:
            body['added_references'] = added
    await call_route(
        vision_route.confirm_task_image,
        task_id,
        body,
        current_user=ctx.user,
    )
    out = {
        'task_id': task_id,
        'ok': True,
        'action': action,
        'request_id': request_id,
        'note': (
            'The decision was delivered; the task agent continues with '
            'it. Follow the task with wait_task; the image lands in '
            'generated_images/ (task_files shows it).'
        ),
    }
    if action == 'confirm':
        out['model'] = body.get('model') or pending.get('model')
        if body.get('added_references'):
            out['added_references'] = body['added_references']
    if notes:
        out['notes'] = notes
    return out
