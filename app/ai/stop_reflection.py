"""The Stop hook's reflection turn — block once, approve on retry.

Split out of ``claude_backend_hooks._handle_hook_callback`` (the
dispatch kept its shape; this owns the branch): the first stop saves
the real result and BLOCKS with the reflection prompt, the retry
(``stop_hook_active``) approves. The VIBE_TASK_REFLECTION switch
(default ``on`` — production unchanged, the ONE read lives here) ends
the turn at the first stop instead; the review/exec gates and the
result save above it stand either way.
"""

import json

from app.ai.reflection_guard import begin_reflection, end_reflection
from app.env_options import Options
from app.prompts import (
    REFLECTION_PROMPT,
    SCHEDULED_WATERMARK_PROMPT,
    render_prompt,
)


async def handle(session, request_id: str, tool_input: dict) -> None:
    """Serve one ``stop_reflection`` hook callback for *session*."""
    if tool_input.get('stop_hook_active'):
        end_reflection(session)  # bracket-end; see reflection_started
        _ev = json.dumps({'event': 'reflection_completed'})
        await session._emit_message('agent_event', _ev)
        await session._send_hook_response(request_id, {'decision': 'approve'})
        return
    if await session._deny_stop_if_tasklist_open(request_id):
        return
    if await session._deny_stop_if_async_agents_running(request_id):
        return
    if await session._deny_stop_if_review_unsatisfied(request_id):
        return
    if await session._deny_stop_if_exec_review_unsatisfied(request_id):
        return
    # Save real result before reflection overwrites it. Prefer the
    # full exec-phase transcript (the agent may emit the full report
    # then a brief "Done." closing — the Stop-hook payload's
    # ``last_assistant_message`` only contains the closing). Fall
    # back to that field if we have no accumulated text.
    pre = '\n\n'.join(p for p in session._exec_phase_text_parts if p)
    if not pre:
        pre = tool_input.get('last_assistant_message', '')
    # Always set (even to '') so the result handler can detect that
    # Stop-hook reflection fired. If pre is empty, the
    # post-reflection text in the next ``result`` event is
    # reflection content and MUST NOT become the user-facing answer.
    session._pre_reflection_result = pre
    if pre:
        await session._save_result(pre)
    if Options.TASK_REFLECTION.get() == 'off':
        await session._send_hook_response(request_id, {'decision': 'approve'})
        return
    # Reflection tool calls run after the turn's work is done — they
    # must not count as progress in the Q&A finalize check, nor start
    # new work (reflection_guard).
    begin_reflection(session)
    # Structured marker — makes the reflection PHASE observable
    # independently of model wording.
    _ev = json.dumps({'event': 'reflection_started'})
    await session._emit_message('agent_event', _ev)
    reason = render_prompt(REFLECTION_PROMPT, store_slug=session.store_slug)
    if await session._is_scheduled_task():
        reason += '\n\n' + SCHEDULED_WATERMARK_PROMPT
    await session._send_hook_response(
        request_id,
        {'decision': 'block', 'reason': reason},
    )
