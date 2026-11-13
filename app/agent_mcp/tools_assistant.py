"""The workspace-assistant tool: ``workspace_assistant``.

The workspace assistant is a per-user chat agent for organizing the
workspace (knowledge files, store notes). The web UI talks to it over
``/api/workspace/assistant/*`` and reads its answers off the SSE
stream (``ws_assistant_message`` / ``ws_assistant_done``) — nothing is
persisted to the DB.

This tool drives the SAME routes and reads the reply from the SAME
stream: ``send`` subscribes before it sends, then waits (bounded, like
``wait_task``) for the turn's messages and its ``ws_assistant_turn_done``
(the per-turn completion — the session's process stays alive across
turns, so ``ws_assistant_done`` only fires when it exits). ``status``
reports ``running`` (busy with a turn — false once the reply is in),
``alive`` (the session process lives on, so the next send reuses it)
and the last reply (held in memory by the manager — there is no DB to
read it from); ``stop`` stops it.
"""

import asyncio
import json
import time

from app.agent_mcp.registry import schema, tool
from app.agent_mcp.support import (
    DEFAULT_WAIT_SECONDS,
    MAX_TEXT_CHARS,
    MAX_WAIT_SECONDS,
    McpContext,
    ToolError,
    call_route,
    require,
    trim,
)
from app.events.bus import event_bus
from app.routers import workspace_assistant as assistant_route
from app.routers.workspace_assistant import MessageRequest

# The roles worth relaying. ``delta`` is the streaming prefix of an
# assistant message (the full message follows), ``tool_use`` and
# ``agent_event`` are mechanics — none of them belong in an operator's
# context. ``system`` carries errors; ``result`` is the answer.
_REPLY_ROLES = ('assistant', 'result', 'system')

_ACTIONS = ('send', 'status', 'stop')


async def _collect_reply(session_key: str, timeout: float) -> dict:
    """Read the assistant's turn off the event bus, up to *timeout*.

    Subscribed BEFORE the message is sent (the caller's order), so a
    fast answer cannot slip between send and subscribe. Returns early
    on ``ws_assistant_turn_done`` (the turn's own completion — the
    session process lives on) or on ``ws_assistant_done`` (the process
    exited, with or without finishing the turn); on timeout returns
    whatever arrived.
    """
    queue = event_bus.subscribe()
    messages: list[dict] = []
    try:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {'done': False, 'messages': messages}
            try:
                raw = await asyncio.wait_for(queue.get(), timeout=remaining)
            except TimeoutError:
                return {'done': False, 'messages': messages}
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if data.get('session_key') != session_key:
                continue
            etype = data.get('type')
            if etype == 'ws_assistant_turn_done':
                return {
                    'done': True,
                    'messages': messages,
                    'return_code': None,
                }
            if etype == 'ws_assistant_done':
                return {
                    'done': True,
                    'messages': messages,
                    'return_code': data.get('return_code'),
                }
            if etype == 'ws_assistant_message' and data.get('role') in (
                _REPLY_ROLES
            ):
                messages.append({
                    'role': data.get('role'),
                    'content': trim(data.get('content'), MAX_TEXT_CHARS)[0],
                })
    finally:
        event_bus.unsubscribe(queue)


def _reply_text(messages: list[dict]) -> str | None:
    """The turn's answer: the LAST result text, else the last message."""
    for message in reversed(messages):
        if message.get('role') == 'result':
            return message.get('content')
    return messages[-1].get('content') if messages else None


@tool(
    'workspace_assistant',
    'Drive the workspace assistant — a per-user chat agent that '
    'organizes the workspace (knowledge files, store notes) for you. '
    'action=send delivers a message and waits (default ~45 s) for the '
    'reply, which it returns; a slow turn times out and the reply can '
    'be picked up with action=status. Only ONE send may be in flight '
    'per user at a time — a second send while one is still waiting '
    'for its reply is refused. action=status reports running (busy '
    'with a turn), alive (its session stays up between turns, so the '
    'next send keeps context) and its last reply. action=stop halts '
    'it. The assistant edits workspace files itself; it does not run '
    'store tasks.',
    schema(
        {
            'action': {
                'type': 'string',
                'enum': list(_ACTIONS),
                'description': 'What to do.',
            },
            'message': {
                'type': ['string', 'null'],
                'description': (
                    'send only: what to ask or tell the assistant.'
                ),
            },
            'profile_id': {
                'type': ['string', 'null'],
                'description': (
                    'send only: AI profile for the assistant. Omit for '
                    'the server default.'
                ),
            },
            'wait_seconds': {
                'type': 'integer',
                'minimum': 1,
                'maximum': MAX_WAIT_SECONDS,
                'description': (
                    'send only: how long to wait for the reply '
                    f'(default {DEFAULT_WAIT_SECONDS}, max '
                    f'{MAX_WAIT_SECONDS}).'
                ),
            },
        },
        required=['action'],
    ),
)
async def workspace_assistant(ctx: McpContext, args: dict) -> dict:
    action = require(args, 'action')
    if action not in _ACTIONS:
        raise ToolError(
            f'Unknown action {action!r} — accepted: {", ".join(_ACTIONS)}'
        )
    manager = assistant_route.ws_assistant_manager
    session_key = manager.session_key(ctx.user.id)

    if action == 'stop':
        result = await call_route(
            assistant_route.stop_assistant, _user=ctx.user
        )
        return {
            **(result or {'ok': False}),
            'note': (
                'Stopped.'
                if (result or {}).get('ok')
                else 'No assistant session was running.'
            ),
        }

    if action == 'status':
        result = await call_route(assistant_route.get_status, _user=ctx.user)
        last_reply, cut = trim(manager.last_reply(ctx.user.id))
        return {
            **(result or {}),
            'last_reply': last_reply,
            'truncated': cut,
        }

    # action == 'send'
    message = args.get('message')
    if not isinstance(message, str) or not message.strip():
        raise ToolError('message is required for action=send')
    timeout = args.get('wait_seconds') or DEFAULT_WAIT_SECONDS
    timeout = max(1, min(int(timeout), MAX_WAIT_SECONDS))

    # ONE in-flight send per user. The reply arrives on a live-only
    # stream keyed by the USER, not the turn — a second concurrent
    # send would collect the first turn's messages and done event and
    # cross the two replies. Refuse (the manager's lock is held for
    # the whole send-and-wait) instead of racing.
    lock = manager.send_lock(ctx.user.id)
    if lock.locked():
        raise ToolError(
            'Another workspace_assistant send for this user is still '
            'waiting for its reply — wait for it to finish (or poll '
            'workspace_assistant(action=status)) before sending again.'
        )
    async with lock:
        # Subscribe BEFORE sending: the answer can be fast, and there is
        # no replay — the bus is live-only, like the SSE stream the UI
        # reads.
        collector = asyncio.create_task(_collect_reply(session_key, timeout))
        try:
            sent = await call_route(
                assistant_route.send_message,
                MessageRequest(
                    content=message,
                    profile_id=args.get('profile_id') or None,
                ),
                _user=ctx.user,
            )
        except BaseException:
            collector.cancel()
            raise
        outcome = await collector

        reply = _reply_text(outcome['messages'])
        result = {
            'ok': bool((sent or {}).get('ok')),
            # Busy (a turn still awaits its result) — normally False
            # here, the reply being in; the session process itself
            # lives on (``alive``) so the next send reuses it.
            'running': manager.is_running(ctx.user.id),
            'alive': manager.alive(ctx.user.id),
            'done': outcome['done'],
            'timed_out': not outcome['done'],
            'reply': reply,
            'messages': outcome['messages'],
        }
        if outcome['done'] and outcome.get('return_code') not in (0, None):
            result['note'] = (
                f"The assistant's process exited with code "
                f'{outcome.get("return_code")} — the reply may be partial.'
            )
        if not outcome['done']:
            result['note'] = (
                'The assistant is still working. Poll '
                'workspace_assistant(action=status) for its reply.'
            )
        return result
