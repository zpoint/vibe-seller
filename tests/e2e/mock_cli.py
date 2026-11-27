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

import asyncio
import json
import os
import re
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


#: Keep this turn ALIVE for at least this many seconds before its
#: result. 0 (the default) changes nothing.
#:
#: The auto-update leg S2 needs a task that HOLDS the server for a few
#: minutes — that is the whole scenario — and `delay` cannot give it
#: that: `delay` paces every step, so a long-running turn means a plan
#: that takes five minutes to arrive, and the leg that waits for the
#: plan then waits for the approval too. This asks for the one thing
#: the leg actually cares about: time held busy.
MIN_RUN_SECONDS = float(SCENARIO.get('min_run_seconds', 0))
_TURN_STARTED = time.monotonic()


def hold_turn_open() -> None:
    """Sleep until this process has been alive MIN_RUN_SECONDS."""
    left = MIN_RUN_SECONDS - (time.monotonic() - _TURN_STARTED)
    if left > 0:
        time.sleep(left)


def emit_result(text: str):
    hold_turn_open()
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


# The orchestrator-spawn markers: the mock plays the part of an agent
# splitting per-store work into sub-tasks. It reads the vibe-seller
# entry from the launch's --mcp-config file (the SAME registration a
# real task agent is given, written by
# ClaudeCodeBackend._register_vibe_seller_mcp), spawns that server
# over stdio, and calls the real in-task tool vibe_seller_create_task
# once per store listed in the prompt (``stores: <id1>,<id2>``), with
# parent_task_id = its own task id. It then tries the two spawns the
# server must refuse — a no-store child, and (with
# [[MOCK_TRY_GRANDCHILD]]) a child of a sub-task — and echoes every
# tool result into its final text, so the e2e can assert on the
# refusals reaching the transcript.
SPAWN_SUBTASKS_MARKER = '[[MOCK_SPAWN_SUBTASKS]]'
TRY_GRANDCHILD_MARKER = '[[MOCK_TRY_GRANDCHILD]]'
# With this marker too, each spawned child's description carries the
# park marker: the child asks the shipping question and, unanswered,
# parks — a sub-task waiting on the user while its parent waits on it.
CHILD_ASKS_MARKER = '[[MOCK_CHILD_ASKS]]'


def mcp_server_entry() -> dict:
    """The vibe-seller stdio server entry from the launch's MCP config."""
    cfg_path = None
    for i, arg in enumerate(sys.argv):
        if arg == '--mcp-config' and i + 1 < len(sys.argv):
            cfg_path = sys.argv[i + 1]
        elif arg.startswith('--mcp-config='):
            cfg_path = arg.split('=', 1)[1]
    if cfg_path is None:
        # The real CLI auto-discovers the project config from cwd.
        cfg_path = os.path.join(os.getcwd(), '.mcp.json')
    with open(cfg_path, encoding='utf-8') as fh:
        servers = json.load(fh).get('mcpServers', {})
    entry = servers.get('vibe-seller')
    if not entry or not entry.get('command'):
        raise RuntimeError(f'no vibe-seller server entry in {cfg_path}')
    return entry


def arg_after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1] if flag in args else ''


async def _create_via_mcp(entry: dict, bodies: list[tuple[str, dict]]):
    """Call vibe_seller_create_task once per body over the real stdio server."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # Env: the entry carries none (the backend registers command/args/
    # cwd only), and this process's env is the launch env the server
    # built — VIBE_HOME, PATH, the works. A default-minimal env would
    # aim app.config at the wrong home.
    params = StdioServerParameters(
        command=entry['command'],
        args=list(entry.get('args', [])),
        env=dict(entry.get('env') or os.environ),
        cwd=entry.get('cwd'),
    )
    results = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for tag, body in bodies:
                res = await session.call_tool('vibe_seller_create_task', body)
                text = ''.join(
                    getattr(block, 'text', '') or ''
                    for block in (res.content or [])
                )
                results.append((tag, text))
    return results


def run_spawn_subtasks(prompt: str):
    """Spawn per-store children + the two forbidden attempts, echo all."""
    emit_system()
    time.sleep(DELAY)
    entry = mcp_server_entry()
    own_id = arg_after(list(entry.get('args', [])), '--task-id')
    if not own_id:
        raise RuntimeError('vibe-seller MCP entry carries no --task-id')
    found = re.search(r'stores:\s*([0-9a-fA-F,\-\s]+)', prompt)
    stores = (
        [s for s in re.split(r'[,\s]+', found.group(1).strip()) if s]
        if found
        else []
    )
    bodies: list[tuple[str, dict]] = [
        (
            f'SPAWN[{sid[:8]}]',
            {
                'title': f'store work {sid[:8]}',
                'store_id': sid,
                'parent_task_id': own_id,
                'description': (
                    f'{PARK_MARKER} ' if CHILD_ASKS_MARKER in prompt else ''
                )
                + f'Run the store-bound work for {sid}.',
            },
        )
        for sid in stores
    ]
    bodies.append((
        'NO_STORE',
        {
            'title': 'cross-store leftover',
            'parent_task_id': own_id,
            'description': 'No store on purpose — must be refused.',
        },
    ))
    child_ids = []
    try:
        results = asyncio.run(
            asyncio.wait_for(_create_via_mcp(entry, bodies), timeout=60)
        )
    except Exception as exc:  # noqa: BLE001 — report into the transcript
        results = [('MCP_ERROR', f'{type(exc).__name__}: {exc}')]
    lines = []
    for tag, text in results:
        lines.append(f'{tag}: {text}')
        if tag.startswith('SPAWN'):
            try:
                parsed = json.loads(text)
                if parsed.get('id'):
                    child_ids.append(parsed['id'])
            except (json.JSONDecodeError, TypeError):
                pass
    if TRY_GRANDCHILD_MARKER in prompt:
        # A spawn UNDER a sub-task: under the first child this run
        # created, or — when every own spawn was refused — under
        # itself, which is already a sub-task.
        parent_id = child_ids[0] if child_ids else own_id
        store_for = stores[0] if stores else own_id
        try:
            nested = asyncio.run(
                asyncio.wait_for(
                    _create_via_mcp(
                        entry,
                        [
                            (
                                'GRANDCHILD',
                                {
                                    'title': 'nested work',
                                    'store_id': store_for,
                                    'parent_task_id': parent_id,
                                    'description': 'A child of a child.',
                                },
                            )
                        ],
                    ),
                    timeout=60,
                )
            )
            for tag, text in nested:
                lines.append(f'{tag}: {text}')
        except Exception as exc:  # noqa: BLE001
            lines.append(f'GRANDCHILD: {type(exc).__name__}: {exc}')
    final = ' || '.join(lines)
    emit_text(final)
    emit_result(final)


# The store-browser-start marker: run the task's store wrapper for
# real (``browser-use`` resolves through the agent PATH to the
# per-store wrapper), emit its outcome as a real tool_use/tool_result
# pair, and then — if still alive — carry on with more work. The C2
# terminal browser-start contract stops the agent DURING the wrapper's
# start call, so nothing after the pair may appear in the transcript.
# (tests/e2e/test_browser_start_failure_mock.py)
STORE_BROWSER_MARKER = '[[MOCK_STORE_BROWSER]]'
DOWNLOAD_PROOF_MARKER = '[[MOCK_DOWNLOAD_PROOF]]'


def emit_tool_result(tool_use_id: str, output: str, is_error: bool = False):
    """A real tool_result for an earlier tool_use (user message)."""
    emit({
        'type': 'user',
        'message': {
            'role': 'user',
            'content': [
                {
                    'type': 'tool_result',
                    'tool_use_id': tool_use_id,
                    'content': output,
                    'is_error': is_error,
                }
            ],
        },
    })


def _resolve_browser_use() -> str:
    """The STORE WRAPPER this task's agent is supposed to run.

    A real CLI gets the wrapper's dir first on PATH from
    ``build_launch_artifacts`` (``apply_agent_venv_path``) — but a
    MOCK-CLI launch skips that whole artifact build, so the wrapper is
    never on our PATH. Resolving ``shutil.which`` FIRST is wrong even
    when it finds something: a server started under ``uv run`` (CI)
    has the venv's bin on PATH, so ``which`` returns the REAL
    browser-use CLI — the mock then bypasses the store wrapper (and
    its start contract) entirely, the scenario's task completes, and
    the e2e asserts on a flow that never ran (the CI failure this
    fixed). The wrapper comes FIRST — the NEWEST under the server's
    vibe home's ``bin/``: the task's own store wrapper is written
    moments before the agent starts, and the mock e2e runs
    sequentially, so newest-is-ours holds — with ``which`` only as
    the fallback when no wrapper exists yet.

    The home is resolved EXACTLY like the server resolves it
    (``app.config``: ``$VIBE_HOME``, else ``~/.vibe-seller``): the
    server never writes the variable back into the environment, so a
    server started WITHOUT ``VIBE_HOME`` (the default configuration —
    CI's mock server, every packaged install) serves the default home
    while its child mock still sees no ``VIBE_HOME``. Reading only
    the env var made the resolver skip the wrapper glob there and
    fall back to the real CLI again (the second CI failure this
    fixed).
    """
    from pathlib import Path
    import shutil

    home = os.environ.get('VIBE_HOME') or str(Path.home() / '.vibe-seller')
    wrappers = sorted(
        Path(home).glob('bin/*/browser-use'),
        key=lambda p: p.stat().st_mtime,
    )
    if wrappers:
        return str(wrappers[-1])
    exe = shutil.which('browser-use')
    if exe:
        return exe
    raise RuntimeError(
        f'no browser-use wrapper under {home}/bin/ and none on '
        'PATH — the store wrapper was not written before the agent '
        'started'
    )


def run_download_proof():
    """Deterministic download flow for the single-install re-proof.

    Executes the REAL store wrapper with a heredoc that opens the
    local fixture page, clicks its 下载报表 button, waits for the
    file, then lists the store's download dir. No LLM anywhere — the
    mock's script is the agent. The outer driver (or test) asserts
    the file landed in <sandbox home>/.vibe-seller/downloads/<slug>/.
    """
    import subprocess

    emit_system()
    time.sleep(DELAY)
    browser_use = _resolve_browser_use()
    heredoc = (
        'import json as _json\n'
        'new_tab("http://127.0.0.1:18770/")\n'
        'import time\n'
        'time.sleep(1)\n'
        'box = _json.loads(js(\'var e=document.querySelector("#download"); '
        'var r=e.getBoundingClientRect(); '
        'return JSON.stringify({x:Math.round(r.x+r.width/2),'
        "y:Math.round(r.y+r.height/2)})'))\n"
        'click_at_xy(box["x"], box["y"])\n'
        'time.sleep(4)\n'
    )
    tool_id = 'toolu_mock_download_1'
    emit({
        'type': 'assistant',
        'message': {
            'role': 'assistant',
            'content': [
                {
                    'type': 'tool_use',
                    'id': tool_id,
                    'name': 'Bash',
                    'input': {
                        'command': (f"{browser_use} <<'PY'\n{heredoc}PY"),
                    },
                }
            ],
        },
    })
    proc = subprocess.run(
        [browser_use],
        input=heredoc,
        capture_output=True,
        text=True,
        timeout=180,
    )
    output = (proc.stdout or '') + (proc.stderr or '')
    emit_tool_result(tool_id, output, is_error=proc.returncode != 0)
    listing = subprocess.run(
        [
            'bash',
            '-c',
            'ls -lt "${VIBE_STORE_DOWNLOADS:-$HOME/.vibe-seller/'
            'downloads}/${VIBE_STORE_SLUG:-c-proof-store}" 2>&1 | head -5',
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    final = 'download proof done\n' + (listing.stdout or '')
    emit_text(final)
    emit_result(final)


def run_store_browser_start():
    """Execute the store wrapper, then try to keep working.

    The follow-up tool call + result exist to prove the agent was
    stopped: when the start fails terminally, the server fails the
    task and kills this process inside the wrapper's start call, so
    they never reach the transcript. When the start succeeds (or the
    failure is not terminal), they do.
    """
    import subprocess

    emit_system()
    time.sleep(DELAY)
    browser_use = _resolve_browser_use()
    tool_id = 'toolu_mock_browser_start_1'
    emit({
        'type': 'assistant',
        'message': {
            'role': 'assistant',
            'content': [
                {
                    'type': 'tool_use',
                    'id': tool_id,
                    'name': 'Bash',
                    'input': {
                        'command': f'{browser_use} -c \'print("open")\'',
                    },
                }
            ],
        },
    })
    proc = subprocess.run(
        [browser_use, '-c', 'print("open")'],
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = (proc.stdout or '') + (proc.stderr or '')
    emit_tool_result(tool_id, output, is_error=proc.returncode != 0)
    # Still alive: carry on like an agent that was NOT stopped.
    emit_tool_calls([
        {
            'tool': 'Bash',
            'input': {'command': 'echo still-alive'},
        }
    ])
    final = 'browser start call finished; kept working'
    emit_text(final)
    emit_result(final)


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


# Question-park flows (an unanswered question parks the task; a
# scheduled run's question is declined). Placeholders only.
PARK_MARKER = '[[MOCK_ASK_PARK]]'
ASK_ON_EXECUTE_MARKER = '[[MOCK_ASK_ON_EXECUTE]]'
ANSWERED_PREFIX = 'The operator answered your earlier AskUserQuestion:'
SHIPPING_QUESTIONS = [
    {
        'question': 'Which shipping option?',
        'header': 'Shipping',
        'options': [
            {'label': 'Standard', 'description': '5 days'},
            {'label': 'Express', 'description': '2 days'},
        ],
        'multiSelect': False,
    }
]


def answer_in_prompt(prompt: str) -> str | None:
    """The answer a resumed run was handed (``- <q> → <a>``), if any."""
    if not prompt.startswith(ANSWERED_PREFIX):
        return None
    for line in prompt.splitlines()[1:]:
        if line.startswith('- ') and ' → ' in line:
            return line.split(' → ', 1)[1].strip()
    return ''


def ask_shipping(request_id: str) -> dict | None:
    """Ask the shipping question; wait with NO deadline of our own.

    Returns the control_response, or None when the server interrupted
    us or closed stdin — what a CLI stopped mid-question sees.
    """
    emit({
        'type': 'control_request',
        'request_id': request_id,
        'request': {
            'tool_name': 'AskUserQuestion',
            'input': {'questions': SHIPPING_QUESTIONS},
        },
    })
    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        try:
            msg = json.loads(line.strip() or '{}')
        except json.JSONDecodeError:
            continue
        if msg.get('type') == 'control_response':
            return msg
        req = (
            msg.get('request', {})
            if msg.get('type') == 'control_request'
            else {}
        )
        if req.get('subtype') == 'interrupt':
            return None


def run_ask_park(prompt: str):
    """Ask, and wait as long as it takes. A resumed run (its prompt
    carries the answer) reports the chosen option instead of asking."""
    emit_system()
    answer = answer_in_prompt(prompt)
    if answer is not None:
        result = f'Resumed. Chosen option: {answer}'
        emit_text(result)
        emit_result(result)
        return
    time.sleep(DELAY)
    response = ask_shipping('park-req-001')
    if response is None:
        sys.exit(130)  # interrupted while waiting, like a stopped CLI
    answers = (
        response.get('response', {})
        .get('response', {})
        .get('updatedInput', {})
        .get('answers', {})
    )
    result = f'Chosen option: {answers.get("Which shipping option?", "")}'
    emit_text(result)
    emit_result(result)


def run_ask_on_execute():
    """A scheduled run's execute turn asks; a decline is reported."""
    emit_system()
    time.sleep(DELAY)
    response = ask_shipping('exec-ask-001')
    if response is None:
        sys.exit(130)
    inner = response.get('response', {}).get('response', {})
    if inner.get('behavior') == 'deny':
        reason = (inner.get('message') or '').split(':')[0]
        result = (
            f'Question declined ({reason}). Chose Standard myself. '
            'Open questions: Which shipping option?'
        )
    else:
        chosen = inner.get('updatedInput', {}).get('answers', {})
        result = f'Chosen option: {chosen.get("Which shipping option?", "")}'
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


def next_stdin_message() -> tuple[str, str] | None:
    """Blocking read of the next meaningful stream-json message on
    stdin: ``('user', text)`` for a user message, ``('interrupt', '')``
    for the SDK interrupt control_request (stop() — the real CLI ends
    itself on an interrupt), None on EOF (the backend closed stdin).
    Other lines (SDK init, control replies) are skipped, like the real
    CLI's reader."""
    while True:
        line = sys.stdin.readline()
        if not line:  # EOF
            return None
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mtype = msg.get('type')
        if mtype == 'user':
            content = msg.get('message', {}).get('content', '')
            if isinstance(content, list):
                content = ' '.join(
                    b.get('text', '') for b in content if isinstance(b, dict)
                )
            return 'user', content or ''
        if mtype == 'control_request':
            request = msg.get('request', {})
            if request.get('subtype') == 'interrupt':
                return 'interrupt', ''


def run_workspace(first_prompt: str):
    """Workspace-assistant lifecycle: ONE process serves every turn,
    exactly like ``claude -p --input-format stream-json``.

    The old mock exited after its single result, so CI could never see
    the per-turn-completion bug class: the real CLI keeps its stdin
    open across turns, emits one ``result`` per turn, and exits only
    when the backend closes stdin (session stop). This loop models
    that: answer, emit the turn's result, then wait for the next user
    message on the live channel. Exit paths: stdin EOF, or the SDK
    interrupt control_request stop() sends to an idle session."""
    emit_system()
    message = first_prompt
    turn = 0
    while True:
        turn += 1
        # Echo what this turn was asked about, so a transcript maps
        # replies to requests (the real CLI's answers differ per turn
        # too).
        reply = f'{RESULT} — {message[:40]!r} (turn {turn})'
        emit_text(reply)
        emit_result(reply)
        nxt = next_stdin_message()
        if nxt is None or nxt[0] == 'interrupt':
            return
        message = nxt[1]


if __name__ == '__main__':
    # Determine mode from args (matches real claude CLI interface).
    # '--mode=workspace' (the workspace-assistant session's launch) is
    # its OWN lifecycle: one process, many turns, exit on stdin EOF —
    # the real CLI never exits after a result with stdin open, and the
    # assistant's running state depends on that, so the mock must
    # model it. The mode is read from the EXACT ``--mode=`` value
    # (ClaudeCodeBackend passes ``--mode={self.mode}``), never from a
    # substring of any argument: the launch also carries --add-dir /
    # --settings paths, and a checkout or VIBE_HOME path containing
    # "auto" once turned every plan-mode task into an execute run.
    given = next(
        (
            a.removeprefix('--mode=')
            for a in sys.argv
            if a.startswith('--mode=')
        ),
        'plan_then_execute',
    )
    if given == 'workspace':
        mode = 'workspace'
    elif given == 'plan_then_execute':
        mode = 'plan_then_execute'
    else:
        mode = 'execute'  # auto / execute behave alike in the mock

    # Peek at the assembled prompt: if it carries the ask marker, run
    # the AskUserQuestion echo flow regardless of mode. Consuming the
    # initial user message here is harmless for the other flows — they
    # only read control_response messages later.
    prompt = read_initial_prompt()
    if mode == 'workspace':
        run_workspace(prompt)
    elif PARK_MARKER in prompt:
        run_ask_park(prompt)
    elif ASK_ON_EXECUTE_MARKER in prompt and mode == 'execute':
        run_ask_on_execute()
    elif ASK_MARKER in prompt:
        run_ask_question()
    elif ASYNC_MARKER in prompt:
        run_async_subagent()
    elif PREMATURE_MARKER in prompt:
        run_premature_result()
    elif LINGER_MARKER in prompt:
        run_linger_wait()
    elif OUTPUT_FILES_MARKER in prompt:
        run_output_files()
    elif DOWNLOAD_PROOF_MARKER in prompt:
        run_download_proof()
    elif STORE_BROWSER_MARKER in prompt:
        run_store_browser_start()
    elif SPAWN_SUBTASKS_MARKER in prompt:
        run_spawn_subtasks(prompt)
    elif mode == 'plan_then_execute':
        run_plan_then_execute()
    else:
        run_execute()
