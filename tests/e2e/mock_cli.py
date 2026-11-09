#!/usr/bin/env python3
"""Mock Claude CLI that outputs stream-json events.

Used by MOCK_CLI mode to test the full pipeline:
subprocess → _handle_event() → _emit_message/_emit_ephemeral → SSE → frontend

Reads scenario from MOCK_CLI_SCENARIO env var (JSON) or uses defaults.
Supports plan_then_execute and execute modes via command-line args.

Responds to stdin JSON for:
- control_response (plan approval/rejection)
- user messages (follow-up instructions)
"""

import json
import os
import select
import sys
import time

# Read scenario from env
_raw = os.environ.get('MOCK_CLI_SCENARIO', '{}')
try:
    SCENARIO = json.loads(_raw)
except json.JSONDecodeError:
    SCENARIO = {}

PLAN = SCENARIO.get('plan', '## Test Plan\n1. Step one\n2. Step two')
RESULT = SCENARIO.get('result', 'Task completed successfully')
TOOL_CALLS = SCENARIO.get(
    'tool_calls',
    [
        {'tool': 'Read', 'input': {'file_path': 'app/models.py'}},
        {'tool': 'Grep', 'input': {'pattern': 'class Task'}},
    ],
)
THINKING = SCENARIO.get('thinking', 'Let me analyze the code...')
SKIP_PLAN = SCENARIO.get('skip_plan', False)
DELAY = SCENARIO.get('delay', 0.05)
# The model the mock's API calls report. A Claude id on the default
# profile is priced from the bundled models.dev snapshot (anthropic).
MODEL = SCENARIO.get('model', 'claude-opus-5-5')


def emit(event: dict):
    """Write a stream-json event to stdout."""
    print(json.dumps(event), flush=True)


# Usage shape per mock API call, like the real CLI's partial messages:
# message_start carries the prompt side (a cache-read context that
# grows with every call), message_delta the final output count, and the
# assistant event repeats the message id + a provisional usage.
_calls = 0


def _usage(n: int, output: int = 1) -> dict:
    return {
        'input_tokens': 800,
        'cache_read_input_tokens': 12_000 * n,
        'cache_creation_input_tokens': 2_000,
        'output_tokens': output,
    }


def _begin_call() -> tuple[str, dict]:
    global _calls
    _calls += 1
    msg_id = f'msg_mock_{os.getpid()}_{_calls}'
    emit({
        'type': 'stream_event',
        'event': {
            'type': 'message_start',
            'message': {'id': msg_id, 'model': MODEL, 'usage': _usage(_calls)},
        },
    })
    return msg_id, _usage(_calls)


def _end_call() -> None:
    emit({
        'type': 'stream_event',
        'event': {'type': 'message_delta', 'usage': {'output_tokens': 250}},
    })


def _message(msg_id: str, usage: dict, content: list) -> dict:
    return {
        'id': msg_id,
        'model': MODEL,
        'role': 'assistant',
        'usage': usage,
        'content': content,
    }


def emit_system():
    emit({'type': 'system', 'session_id': 'mock-session-001'})


def emit_thinking(text: str):
    """Emit thinking as streaming deltas then complete block."""
    # Streaming deltas
    for chunk in [text[i : i + 20] for i in range(0, len(text), 20)]:
        emit({
            'type': 'content_block_delta',
            'delta': {'type': 'thinking_delta', 'thinking': chunk},
        })
        time.sleep(DELAY / 5)
    # Complete assistant event with thinking block
    msg_id, usage = _begin_call()
    emit({
        'type': 'assistant',
        'message': _message(
            msg_id, usage, [{'type': 'thinking', 'thinking': text}]
        ),
    })
    _end_call()


def emit_tool_calls(tools: list[dict]):
    """Emit tool_use blocks in an assistant event."""
    content = []
    for tc in tools:
        content.append({
            'type': 'tool_use',
            'name': tc.get('tool', 'Unknown'),
            'input': tc.get('input', {}),
            'id': f'tool_{hash(json.dumps(tc)) % 10000}',
        })
    if content:
        msg_id, usage = _begin_call()
        emit({
            'type': 'assistant',
            'message': _message(msg_id, usage, content),
        })
        _end_call()


def emit_text(text: str):
    """Emit text as streaming deltas then complete assistant event."""
    for chunk in [text[i : i + 30] for i in range(0, len(text), 30)]:
        emit({
            'type': 'content_block_delta',
            'delta': {'type': 'text_delta', 'text': chunk},
        })
        time.sleep(DELAY / 5)
    msg_id, usage = _begin_call()
    emit({
        'type': 'assistant',
        'message': _message(msg_id, usage, [{'type': 'text', 'text': text}]),
    })
    _end_call()


def emit_exit_plan_mode(plan_text: str):
    """Emit a control_request for ExitPlanMode.

    Uses legacy format matching what _handle_control_request expects:
    top-level request_id + request.tool_name + request.input.
    """
    emit({
        'type': 'control_request',
        'request_id': 'plan-req-001',
        'request': {
            'tool_name': 'ExitPlanMode',
            'input': {'plan': plan_text},
        },
    })


def emit_result(text: str):
    emit({'type': 'result', 'result': text})


# Marker the backend-assembled prompt carries (via task title) to make
# the mock ask an AskUserQuestion and echo back the answer it receives.
# Lets the e2e-mock-cli CI job exercise the full QuestionBanner → backend
# → control_response path without a real LLM (issue #211).
ASK_MARKER = '[[MOCK_ASK_FREE_TEXT]]'
# The '(free-text-e2e)' tag lets the session-wide SSE auto-answer in
# tests/e2e/conftest.py recognise this question and DEFER it to the UI
# test, instead of auto-answering it with the first option. Keep the
# tag in sync with MANUAL_ANSWER_TAG there.
ASK_QUESTION = 'Which marketplaces should I audit? (free-text-e2e)'

# Turn-lifecycle markers (process-per-turn model, PR #87):
# - ASYNC: a well-behaved run — spawn an async subagent, WAIT for its
#   task-notification, then emit the final result and exit.
# - PREMATURE: the backstop path — emit a result while the subagent is
#   still running; the backend must redrive instead of ending the
#   turn; the mock then finishes properly.
# - LINGER: emit the result and then stay alive reading stdin until
#   the backend's quiescence watchdog closes it (EOF) — proving the
#   watchdog, not the mock, terminates the turn.
ASYNC_MARKER = '[[MOCK_ASYNC_SUBAGENT]]'
PREMATURE_MARKER = '[[MOCK_PREMATURE_RESULT]]'
LINGER_MARKER = '[[MOCK_LINGER_WAIT]]'

# The producing-task marker: write the two output files a real agent
# leaves behind (its Write tool), so the MCP task-files e2e has real
# files to list/read/zip. cwd IS the task workspace — the backend
# spawns this CLI there (app/ai/claude_backend.py start()).
OUTPUT_FILES_MARKER = '[[MOCK_OUTPUT_FILES]]'
OUTPUT_TEXT = 'quarterly numbers for acme\n' * 10
OUTPUT_PNG = b'\x89PNG\r\n\x1a\n' + b'\x00MOCK-CHART' * 16


def run_output_files():
    """Emit a producing run and drop its deliverables in the cwd."""
    emit_system()
    emit_thinking('Producing the deliverable files...')
    emit_tool_calls([
        {'tool': 'Write', 'input': {'file_path': 'report.txt'}},
        {'tool': 'Write', 'input': {'file_path': 'chart.png'}},
    ])
    with open('report.txt', 'wb') as fh:
        fh.write(OUTPUT_TEXT.encode('utf-8'))
    with open('chart.png', 'wb') as fh:
        fh.write(OUTPUT_PNG)
    final = 'Wrote report.txt and chart.png.'
    emit_text(final)
    emit_result(final)


_SPAWN_ID = 'toolu_mock_spawn_1'


def emit_async_spawn():
    """Main-agent Agent tool_use + the async launch ack."""
    emit({
        'type': 'assistant',
        'message': {
            'role': 'assistant',
            'content': [
                {
                    'type': 'tool_use',
                    'id': _SPAWN_ID,
                    'name': 'Agent',
                    'input': {
                        'description': 'Check the deliverable',
                        'prompt': 'Independently check the output file.',
                    },
                }
            ],
        },
    })
    emit({
        'type': 'user',
        'message': {
            'role': 'user',
            'content': [
                {
                    'type': 'tool_result',
                    'tool_use_id': _SPAWN_ID,
                    'content': (
                        'Async agent launched successfully. (internal '
                        'metadata)\nagentId: mock-subagent-1'
                    ),
                }
            ],
        },
    })


def emit_subagent_activity_and_notification():
    """Subagent events (parent_tool_use_id set) then the completion
    notification the CLI injects into the parent."""
    emit({
        'type': 'assistant',
        'parent_tool_use_id': _SPAWN_ID,
        'message': {
            'role': 'assistant',
            'content': [
                {'type': 'text', 'text': 'Checking the deliverable...'}
            ],
        },
    })
    time.sleep(DELAY)
    emit({
        'type': 'assistant',
        'parent_tool_use_id': _SPAWN_ID,
        'message': {
            'role': 'assistant',
            'content': [
                {'type': 'text', 'text': 'Check passed: no gaps found.'}
            ],
        },
    })
    time.sleep(DELAY)
    emit({
        'type': 'user',
        'message': {
            'role': 'user',
            'content': [
                {
                    'type': 'text',
                    'text': (
                        '<task-notification tool-use-id="'
                        + _SPAWN_ID
                        + '" status="completed">subagent finished'
                        '</task-notification>'
                    ),
                }
            ],
        },
    })


def wait_for_user_message(timeout: float = 30.0) -> str:
    """Read the next stream-json ``user`` message from stdin (e.g. a
    turn-hold redrive) and return its text; '' on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if select.select([sys.stdin], [], [], 0.1)[0]:
            line = sys.stdin.readline()
            if not line:
                return ''
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get('type') == 'user':
                content = msg.get('message', {}).get('content', '')
                if isinstance(content, list):
                    content = ' '.join(
                        b.get('text', '')
                        for b in content
                        if isinstance(b, dict)
                    )
                return content or ''
    return ''


def run_async_subagent():
    """Well-behaved async flow: wait for the subagent's notification
    BEFORE the final result — no redrive should fire."""
    emit_system()
    time.sleep(DELAY)
    emit_thinking('Producing the deliverable, then a background check.')
    emit_async_spawn()
    time.sleep(DELAY)
    emit_subagent_activity_and_notification()
    time.sleep(DELAY)
    final = f'{RESULT} (subagent confirmed: no gaps)'
    emit_text(final)
    emit_result(final)


def run_premature_result():
    """Backstop flow: result emitted while the subagent still runs.
    The backend must REDRIVE (not end the turn); on the redrive
    message the mock completes the subagent's work and finishes properly."""
    emit_system()
    time.sleep(DELAY)
    emit_async_spawn()
    time.sleep(DELAY)
    emit_result('premature: subagent still running in the background')
    # The backend's redrive arrives as a user message on stdin.
    redrive = wait_for_user_message(timeout=60.0)
    if 'subagent' not in redrive:
        emit_text(f'ERROR: expected a redrive, got: {redrive[:120]!r}')
        emit_result('mock error: no redrive received')
        return
    emit_subagent_activity_and_notification()
    time.sleep(DELAY)
    final = f'{RESULT} (finished after the redrive + subagent)'
    emit_text(final)
    emit_result(final)


def run_linger_wait():
    """Emit the result then STAY ALIVE until the backend's quiescence
    watchdog closes stdin (readline returns EOF) — the mock never
    exits on its own, exactly like the real CLI."""
    emit_system()
    time.sleep(DELAY)
    emit_text(RESULT)
    emit_result(RESULT)
    while True:
        line = sys.stdin.readline()
        if not line:  # EOF — the watchdog closed the pipe
            break


def read_initial_prompt(max_lines: int = 10) -> str:
    """Read the first stream-json ``user`` message from stdin and
    return its text. Skips the SDK initialize control_request and any
    other non-user lines.

    Uses a blocking ``readline()`` rather than ``select()`` on purpose:
    the backend sends the SDK initialize then the user message back to
    back at startup, so the first ``readline()`` buffers BOTH lines.
    A subsequent ``select()`` on the raw fd would then report
    not-readable (the user line is already in Python's buffer, not the
    OS pipe) and block until timeout. Blocking reads avoid that race —
    the user message is always sent, so it always arrives."""
    for _ in range(max_lines):
        line = sys.stdin.readline()
        if not line:  # EOF
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get('type') == 'user':
            content = msg.get('message', {}).get('content', '')
            if isinstance(content, list):
                content = ' '.join(
                    b.get('text', '') for b in content if isinstance(b, dict)
                )
            return content or ''
    return ''


def render_answers(questions: list[dict], answers: dict) -> str:
    """Render answers the way claude-code does — by iterating the asked
    questions and looking each up *by question text*. Mirrors the real
    ``mapToolResultToToolResultBlockParam`` so the rendered text is
    empty exactly when the agent's would be."""
    segments = []
    for q in questions:
        q_text = q.get('question', '')
        ans = answers.get(q_text)
        if not ans:
            continue
        segments.append(f'{q_text} => {ans}')
    return '; '.join(segments)


def run_ask_question():
    """Ask one AskUserQuestion, then echo the operator's answer back
    into the conversation so the UI test can assert it survived."""
    emit_system()
    time.sleep(DELAY)
    questions = [
        {
            'question': ASK_QUESTION,
            'header': 'Scope',
            'options': [
                {'label': 'All marketplaces', 'description': 'every store'},
                {'label': 'US only', 'description': 'United States'},
            ],
        }
    ]
    emit({
        'type': 'control_request',
        'request_id': 'ask-req-001',
        'request': {
            'tool_name': 'AskUserQuestion',
            'input': {'questions': questions},
        },
    })

    response = wait_for_stdin_response(timeout=120.0)
    answers = {}
    if response:
        answers = (
            response.get('response', {})
            .get('response', {})
            .get('updatedInput', {})
            .get('answers', {})
        )
    rendered = render_answers(questions, answers)
    result = f'Understood. You answered: {rendered}'
    emit_text(result)
    emit_result(result)


def wait_for_stdin_response(timeout: float = 30.0):
    """Read and return a control_response JSON message from stdin.

    Skips non-response messages (SDK initialize, user prompts)
    that may be buffered before the actual approval response.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if select.select([sys.stdin], [], [], 0.1)[0]:
            line = sys.stdin.readline().strip()
            if line:
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Only return control_response messages (approval/deny)
                if msg.get('type') == 'control_response':
                    return msg
                # Skip other messages (SDK init, user prompts)
    return None


def run_plan_then_execute():
    """Simulate plan_then_execute mode."""
    emit_system()
    time.sleep(DELAY)

    # Planning phase: thinking + tool calls
    if THINKING:
        emit_thinking(THINKING)
        time.sleep(DELAY)

    if TOOL_CALLS:
        emit_tool_calls(TOOL_CALLS)
        time.sleep(DELAY)

    if SKIP_PLAN:
        # Skip planning — emit result directly
        emit_text(RESULT)
        emit_result(RESULT)
        return

    # Emit plan text then ExitPlanMode request
    emit_text(f'Here is my plan:\n{PLAN}')
    time.sleep(DELAY)
    emit_exit_plan_mode(PLAN)

    # Wait for approval/rejection via stdin
    response = wait_for_stdin_response()
    if not response:
        return

    # Check if approved or rejected
    resp_data = response.get('response', {}).get('response', {})
    behavior = resp_data.get('behavior', '')

    if behavior == 'deny':
        # Rejected — replan
        revised = PLAN + '\n\n(revised based on feedback)'
        emit_thinking('Revising the plan based on feedback...')
        time.sleep(DELAY)
        emit_text(f'Revised plan:\n{revised}')
        time.sleep(DELAY)
        emit_exit_plan_mode(revised)

        # Wait for second approval
        response2 = wait_for_stdin_response()
        if not response2:
            return

    # Execution phase
    time.sleep(DELAY)
    emit_thinking('Executing the plan now...')
    time.sleep(DELAY)
    if TOOL_CALLS:
        emit_tool_calls(TOOL_CALLS[:1])  # Fewer tools in execution
        time.sleep(DELAY)
    emit_text(RESULT)
    emit_result(RESULT)


def run_execute():
    """Simulate execute-only mode."""
    emit_system()
    time.sleep(DELAY)
    if THINKING:
        emit_thinking(THINKING)
        time.sleep(DELAY)
    if TOOL_CALLS:
        emit_tool_calls(TOOL_CALLS)
        time.sleep(DELAY)
    emit_text(RESULT)
    emit_result(RESULT)


if __name__ == '__main__':
    # Determine mode from args (matches real claude CLI interface).
    # 'workspace' (the workspace-assistant session's mode) maps to the
    # execute flow: the assistant has no plan gate, and its handler
    # auto-allows any control request anyway.
    mode = 'plan_then_execute'
    for arg in sys.argv:
        if 'auto' in arg or 'workspace' in arg:
            mode = 'execute'  # auto behaves like execute in mock
            break
        if 'execute' in arg and 'plan' not in arg:
            mode = 'execute'
            break

    # Peek at the assembled prompt: if it carries the ask marker, run
    # the AskUserQuestion echo flow regardless of mode. Consuming the
    # initial user message here is harmless for the other flows — they
    # only read control_response messages later.
    prompt = read_initial_prompt()
    if ASK_MARKER in prompt:
        run_ask_question()
    elif ASYNC_MARKER in prompt:
        run_async_subagent()
    elif PREMATURE_MARKER in prompt:
        run_premature_result()
    elif LINGER_MARKER in prompt:
        run_linger_wait()
    elif OUTPUT_FILES_MARKER in prompt:
        run_output_files()
    elif mode == 'plan_then_execute':
        run_plan_then_execute()
    else:
        run_execute()
