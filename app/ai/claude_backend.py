"""Claude Code CLI backend for AI agent.

Spawns `claude` as a subprocess with bidirectional stream-json protocol:
  -p --print                   (non-interactive / headless mode)
  --input-format stream-json   (send prompts via stdin as JSON)
  --output-format stream-json  (structured streaming output on stdout)
  --permission-mode plan|bypassPermissions (native plan mode or full access)
  --permission-prompt-tool stdio (control protocol for hooks)
  --add-dir ~/.vibe-seller     (workspace with skills + knowledge)
  --append-system-prompt       (injects store context)

Native plan mode flow:
  1. Agent starts with --permission-mode plan (read-only tools)
  2. SDK initialize message configures PreToolUse hooks
  3. Agent calls ExitPlanMode → HookCallback → CanUseTool
  4. Backend captures plan, optionally waits for user approval
  5. Approve with updatedPermissions: SetMode → bypassPermissions
  6. Agent continues executing in same session with full access
"""

import asyncio
import json
import logging
import os
from pathlib import Path
import signal
import sys
import uuid

from sqlalchemy import select

from app.ai.claude_backend_hooks import _HookMixin
from app.ai.claude_backend_stream import _StreamMixin
from app.ai.claude_backend_utils import (
    AGENT_DEBUG,
    AUTO_APPROVE_CALLBACK,
    DRAIN_TIMEOUT,
    INTERRUPT_TIMEOUT,
    MAX_REPEAT_TOOL_CALLS,
    SIGNAL_TIMEOUT,
    STOP_REFLECTION_CALLBACK,
    TOOL_APPROVAL_CALLBACK,
    apply_agent_venv_path,
    resolve_claude_binary,
)
from app.ai.compaction import build_history_prompt, dump_history_file
from app.ai.profiles import DEFAULT_PROFILE_ID, ProfileManager
from app.auth import create_token
from app.browser.manager import (
    atomic_write_json,
    read_mcp_config,
)
from app.browser.process_utils import kill_with_escalation, taskkill_tree
from app.config import BACKEND_PORT, BASE_DIR
from app.database import async_session
from app.env_options import Options
from app.models.task import Task
from app.models.user import User
from app.platform import IS_WINDOWS, find_processes_by_pattern
from app.workspace.manager import (
    VIBE_SELLER_DIR,
    workspace_manager,
)

logger = logging.getLogger(__name__)


def permission_mode_for_agent(agent_mode: str) -> str:
    """Translate the agent's logical mode to Claude Code's
    ``--permission-mode`` CLI flag value.

    - ``plan_then_execute`` → ``plan`` (agent starts in plan mode so
      ExitPlanMode is valid; we transition to bypass via SetMode on
      approval).
    - Anything else (``execute``, ``auto``, ...) → ``bypassPermissions``.

    This is a hot rule: break the ``plan_then_execute`` branch and
    plan-only Tasks can't call ExitPlanMode (Claude Code returns
    ``"You are not in plan mode"``). A unit test pins the mapping.
    """
    return 'plan' if agent_mode == 'plan_then_execute' else 'bypassPermissions'


class AgentSession(_HookMixin, _StreamMixin):
    """Manages a single claude -p subprocess for a task."""

    def __init__(
        self,
        task_id: str,
        prompt: str,
        store_slug: str | None = None,
        system_prompt_extra: str = '',
        mode: str = 'execute',
        profile_id: str = DEFAULT_PROFILE_ID,
        message_history: list[dict] | None = None,
        no_store: bool = False,
        auto_approve_plan: bool = False,
        task_dir: Path | None = None,
        skip_reflection: bool = False,
    ):
        self.task_id = task_id
        self.prompt = prompt
        self.store_slug = store_slug
        self.system_prompt_extra = system_prompt_extra
        self.mode = mode  # "execute" or "plan_then_execute"
        self.profile_id = profile_id
        self.message_history = message_history or []
        self.no_store = no_store
        self.auto_approve_plan = auto_approve_plan
        self.task_dir = task_dir
        self.skip_reflection = skip_reflection
        self.resume_session_id: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self._result_text = ''
        self._last_result_event = ''  # plan-skip fallback
        self._agent_success = False  # CLI reported success
        self._is_error_result = False
        self._pre_reflection_result: str | None = None
        self._error_category: str | None = None
        self.session_id: str | None = None
        # Plan approval state (native plan mode)
        self._pending_plan_request_id: str | None = None
        self._plan_approval_event: asyncio.Event = asyncio.Event()
        self._plan_approved: bool = False
        self._plan_saved: bool = False
        # Session-lifecycle events. `done` is an idempotent
        # end-of-session signal — typically set from `_stream_output`'s
        # `finally` (normal, cancelled, or error path), with a
        # defensive fallback set in `stop()` for the corner case where
        # `start()` died before the reader was spawned. `asyncio.Event`
        # sets are idempotent, so repeat sets are harmless; waiters
        # observe the first. Callers should await it instead of
        # polling `running` / `is_running`.
        # `plan_saved_event` fires when `_save_design_plan` commits a
        # plan; interactive plan-mode waiters use it to hand ownership
        # to `execute_planned_task` while the session stays alive
        # waiting for user approval. Cleared on approve/reject so it
        # doesn't short-circuit a subsequent wait after the caller
        # resumes execution.
        self.done: asyncio.Event = asyncio.Event()
        self.plan_saved_event: asyncio.Event = asyncio.Event()
        self._executing: bool = mode in ('execute', 'auto')
        self._first_result_emitted: bool = False
        # Every assistant text emitted during the exec phase, in
        # order. Used by the Stop-hook handler to capture multi-
        # message output — e.g. an agent that writes the full report
        # then a brief "Done." closing would otherwise lose the body,
        # because the SDK's Stop-hook payload exposes only the literal
        # last message. Reset by each session.start().
        self._exec_phase_text_parts: list[str] = []
        # Control protocol state for AskUserQuestion
        self._pending_questions: dict[
            str, dict
        ] = {}  # request_id -> control_request
        self._answer_events: dict[
            str, asyncio.Event
        ] = {}  # request_id -> event
        self._answers: dict[str, dict] = {}  # request_id -> answers dict
        # Snapshot of pending questions at teardown, read by the task
        # runner after the subprocess exits. Survives past the hook
        # handler's `.pop()` on each request so an agent that exits
        # mid-AskUserQuestion can be parked in WAITING instead of
        # failing silently.
        self._last_pending_questions: dict[str, dict] = {}
        # Whether the agent produced any tool_use block at all.
        # Used by task_runner to detect text-only exits (agent
        # wrote questions as prose instead of calling
        # AskUserQuestion).
        self._had_tool_use: bool = False
        self._stopping: bool = False
        # True once the agent has emitted its final ``result`` event
        # for the current turn — at which point we close stdin so the
        # CLI exits.  Future follow-up messages MUST start a fresh
        # ``--resume`` session, not write to the dying process: the
        # write would silently land in a closed pipe.  This flag lets
        # ``running`` reflect "session is input-capable" rather than
        # the looser "process not yet reaped", closing a race the
        # router used to lose when a follow-up POST arrived in the
        # ~100ms window between result and proc exit.
        self._input_closed: bool = False
        # Circuit breaker: track recent tool call signatures
        self._recent_tool_calls: list[str] = []
        # PreToolUse-hook state. See app.ai.bash_safety and
        # app.ai.claude_backend_utils.check_skill_prereqs.
        self._loaded_skills: set[str] = set()
        self._catalog_read: bool = False
        # Serialize message persistence so seq + created_at stay in order
        self._emit_lock: asyncio.Lock = asyncio.Lock()

    def _check_tool_loop(self, tool_name: str, tool_input: dict) -> bool:
        """Return True if agent is stuck in a degenerate loop.

        Tracks the last N tool call signatures. If all N are
        identical, the agent is looping on garbage calls.
        """
        sig = f'{tool_name}:{json.dumps(tool_input, sort_keys=True)}'
        self._recent_tool_calls.append(sig)
        if len(self._recent_tool_calls) > MAX_REPEAT_TOOL_CALLS:
            self._recent_tool_calls = self._recent_tool_calls[
                -MAX_REPEAT_TOOL_CALLS:
            ]
        if len(self._recent_tool_calls) >= MAX_REPEAT_TOOL_CALLS:
            if len(set(self._recent_tool_calls)) == 1:
                return True
        return False

    async def _cleanup_browser_daemons(self):
        """Kill browser-use daemons spawned for this task.

        Two patterns are checked:
        1. Full UUID in ``--cdp-url`` (Ziniao daemons)
        2. 8-char prefix in ``--session`` (Chrome daemons)

        The 8-char prefix has ~1/4 billion collision chance per
        pair — accepted as a known limitation for best-effort
        cleanup.

        Uses ``find_processes_by_pattern`` (psutil) so it works on
        all platforms — the old ``pgrep``/``os.kill`` path was
        Unix-only.
        """
        if not self.task_id:
            return
        # Pattern 1: full UUID in --cdp-url (Ziniao)
        # Pattern 2: 8-char prefix in --session arg (Chrome),
        #   scoped to --session to avoid overbroad matches
        tid8 = self.task_id[:8]
        try:
            daemons = await find_processes_by_pattern(
                'browser_use.skill_cli.daemon',
            )
            pids: set[int] = set()
            for pid, cmdline in daemons.items():
                if self.task_id in cmdline:
                    pids.add(pid)
                elif '--session' in cmdline and f'-{tid8}' in cmdline:
                    pids.add(pid)
            for pid in pids:
                await kill_with_escalation(pid)
            if pids:
                logger.info(
                    '[%s] Cleaned up %d browser-use daemon(s): %s',
                    self.task_id[:8],
                    len(pids),
                    sorted(pids),
                )
        except Exception:
            pass  # Best-effort cleanup

    async def start(self):
        """Spawn claude and send init messages via stdin."""
        # Note: Per-task daemon cleanup happens in stop(), not here.
        # This avoids a race where a retry could kill its own new daemon.
        # Orphan cleanup is handled by the periodic reaper (daemon_reaper.py).

        # Ensure workspace and venv exist
        await workspace_manager.ensure_init()

        # Permission mode depends on agent mode — see
        # `permission_mode_for_agent` for the mapping. Extracted as a
        # pure helper so a unit test pins the rule (a regression on
        # this line leaves plan-only Tasks unable to call ExitPlanMode).
        perm_mode = permission_mode_for_agent(self.mode)

        mock_cli = Options.MOCK_CLI.get()
        if mock_cli:
            # Test mode: use mock CLI script instead of real claude.
            # Resolve to absolute path since cwd may differ.
            mock_cli_abs = os.path.abspath(mock_cli)
            cmd = [
                sys.executable,
                mock_cli_abs,
                f'--mode={self.mode}',
            ]
        else:
            cmd = [
                resolve_claude_binary(),
                '-p',
                '--output-format',
                'stream-json',
                '--input-format',
                'stream-json',
                '--verbose',
                '--permission-mode',
                perm_mode,
                '--permission-prompt-tool',
                'stdio',
            ]

        ws_dir = self.task_dir or VIBE_SELLER_DIR
        if not mock_cli:
            # Model is set via ANTHROPIC_MODEL in the subprocess env
            # (built from the profile).  Do NOT pass --model from
            # os.environ — that would use the server's default and
            # ignore per-profile model overrides.

            # Resume previous session if available
            if self.resume_session_id:
                cmd.extend(['--resume', self.resume_session_id])

            # Add workspace directory if it exists
            if ws_dir.exists():
                cmd.extend(['--add-dir', str(ws_dir)])

            # Block global playwright MCP server
            cmd.extend([
                '--disallowedTools',
                'mcp__playwright__*,mcp__browser-use__*',
            ])

            # Register vibe-seller MCP server (must run before the
            # mcp_json check so the file exists in the task dir).
            await self._register_vibe_seller_mcp(ws_dir)

            # Optionally restrict MCP to workspace-only servers.
            # Global MCP configs can add 200+ tool definitions that
            # bloat the prompt and slow down third-party LLM APIs.
            mcp_json = ws_dir / '.mcp.json'
            if mcp_json.exists() and not ProfileManager.get_load_global_mcp(
                self.profile_id
            ):
                cmd.extend([
                    '--mcp-config',
                    str(mcp_json),
                    '--strict-mcp-config',
                ])

            # Build system prompt (assembled by build_system_extra)
            system_prompt = self.system_prompt_extra or ''

            if AGENT_DEBUG:
                logger.info(
                    'AGENT_DEBUG [%s] system_prompt (%d chars):\n%s',
                    self.task_id[:8],
                    len(system_prompt),
                    system_prompt[:5000],
                )
                logger.info(
                    'AGENT_DEBUG [%s] user_prompt:\n%s',
                    self.task_id[:8],
                    self.prompt[:2000],
                )

            if system_prompt.strip():
                cmd.extend(['--append-system-prompt', system_prompt])

        # Prepare env
        env = ProfileManager.get_env_for_profile(self.profile_id)
        logger.info(
            'Starting agent for task %s (mode=%s, profile=%s, '
            'base_url=%s, model=%s)',
            self.task_id,
            self.mode,
            self.profile_id,
            env.get('ANTHROPIC_BASE_URL', '<default>'),
            env.get('ANTHROPIC_MODEL', '<default>'),
        )
        # Pass task ID for multi-client CDP proxy isolation
        env['VIBE_TASK_ID'] = self.task_id

        # Pin Claude Code's TaskList directory to a per-task path
        # (default is session UUID, which is regenerated per spawn
        # and unreachable from follow-up sessions). The Stop-hook
        # completion gate reads from this exact path.
        env['CLAUDE_CODE_TASK_LIST_ID'] = f'vibe-{self.task_id[:8]}'

        # Claude Code auto-commits the task workspace ("Initial
        # workspace setup") right after `git init`. The workspace
        # is a fresh repo at ~/.vibe-seller/tasks/<id>/, so no
        # `git config --local` on the source checkout reaches it,
        # and many fresh user machines (and CI runners scoped to
        # repo-local identity per #181) have no `git config --global`
        # either. `Author identity unknown` blows up the agent's
        # first commit. Setting GIT_{AUTHOR,COMMITTER}_* via env
        # makes the commit succeed without touching any git config
        # file. setdefault keeps any real identity the user already
        # exported via their shell.
        env.setdefault('GIT_AUTHOR_NAME', 'Vibe Seller Agent')
        env.setdefault('GIT_AUTHOR_EMAIL', 'agent@vibe-seller.local')
        env.setdefault('GIT_COMMITTER_NAME', 'Vibe Seller Agent')
        env.setdefault('GIT_COMMITTER_EMAIL', 'agent@vibe-seller.local')

        # Wire the agent's PATH + VIRTUAL_ENV: store wrapper first, then
        # the shared agent venv (python/pip), then the server venv. The
        # agent's python/pip MUST be the shared venv — the server venv is
        # built without pip on packaged installs, so `pip install` there
        # fails and the agent falls back to a stray system Python. See
        # apply_agent_venv_path.
        apply_agent_venv_path(env, self.store_slug)

        # start_new_session=True puts claude and every descendant into
        # a fresh POSIX session/process group. We do not rely on this
        # to stop cross-task pkill (pkill matches by cmdline, not
        # group) — that's enforced in app/ai/bash_safety.py — but the
        # isolated group is what `_force_kill` signals via
        # `os.killpg` so the whole subtree (claude → MCP server →
        # tools) tears down atomically.
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=100 * 1024 * 1024,
            env=env,
            cwd=str(ws_dir) if ws_dir.exists() else None,
            start_new_session=True,
        )

        # Start readers FIRST so we can process responses to
        # SDK initialize and user messages as they arrive.
        self._stderr_task = asyncio.create_task(self._stream_stderr())
        self._task = asyncio.create_task(self._stream_output())

        # Send SDK initialize with hooks BEFORE user messages.
        # plan_then_execute: hooks for ExitPlanMode + auto-approve
        # auto/execute: hooks for AskUserQuestion + circuit breaker
        if self.mode == 'plan_then_execute':
            await self._send_sdk_initialize()
        elif self.mode in ('auto', 'execute'):
            await self._send_sdk_initialize_auto()

        # Persist the initial user prompt so it appears in the
        # history file when sessions are reconstructed (e.g. on
        # profile switch).  Skip when replaying history — those
        # messages are already in the DB.
        if not self.message_history:
            await self._emit_message('user', self.prompt)

        # Build the user prompt, optionally embedding prior
        # conversation history.  Instead of stuffing ALL messages
        # into the prompt (which can overflow the context window),
        # dump full history to a JSON file and embed only the
        # last few messages inline.  The agent is instructed to
        # read the file before proceeding.
        prompt = self.prompt
        if not self.resume_session_id and self.message_history:
            history_file = dump_history_file(self.task_id, self.message_history)
            history_ctx = build_history_prompt(
                self.message_history, history_file
            )
            if history_ctx:
                prompt = history_ctx + '\n\n---\n\n' + prompt

        await self._send_stdin({
            'type': 'user',
            'message': {'role': 'user', 'content': prompt},
        })

    async def _send_sdk_initialize(self):
        """Send SDK control request to configure hooks."""
        # ExitPlanMode + AskUserQuestion → interactive approval
        # (SDK evaluates matchers in order, first match wins)
        hooks = {
            'PreToolUse': [
                {
                    'matcher': '^(ExitPlanMode|AskUserQuestion)$',
                    'hookCallbackIds': [TOOL_APPROVAL_CALLBACK],
                },
                {
                    'matcher': ('^(?!ExitPlanMode$|AskUserQuestion$).*'),
                    'hookCallbackIds': [AUTO_APPROVE_CALLBACK],
                },
            ],
        }
        if not self.skip_reflection:
            hooks['Stop'] = [
                {'hookCallbackIds': [STOP_REFLECTION_CALLBACK]},
            ]
        await self._send_stdin({
            'type': 'control_request',
            'request_id': str(uuid.uuid4()),
            'request': {
                'subtype': 'initialize',
                'hooks': hooks,
            },
        })

    async def _send_sdk_initialize_auto(self):
        """Send SDK hooks for auto mode.

        AskUserQuestion → forwarded to CanUseTool for interactive
        question handling.  All other tools → auto-approved with
        circuit-breaker loop detection.  No ExitPlanMode hook.
        """
        hooks = {
            'PreToolUse': [
                {
                    'matcher': '^AskUserQuestion$',
                    'hookCallbackIds': [TOOL_APPROVAL_CALLBACK],
                },
                {
                    'matcher': '^(?!AskUserQuestion$).*',
                    'hookCallbackIds': [AUTO_APPROVE_CALLBACK],
                },
            ],
        }
        if not self.skip_reflection:
            hooks['Stop'] = [
                {'hookCallbackIds': [STOP_REFLECTION_CALLBACK]},
            ]
        await self._send_stdin({
            'type': 'control_request',
            'request_id': str(uuid.uuid4()),
            'request': {
                'subtype': 'initialize',
                'hooks': hooks,
            },
        })

    async def _register_vibe_seller_mcp(self, ws_dir):
        """Register the vibe-seller MCP server in .mcp.json."""
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(User).where(User.role == 'ai_bot')
                )
                bot = result.scalars().first()
                if bot:
                    token = create_token(bot.id, 'ai_bot')
                else:
                    token = create_token('system', 'admin')

            mcp_config = read_mcp_config()
            servers = mcp_config.get('mcpServers', {})
            active = {
                name: cfg
                for name, cfg in servers.items()
                if name != 'vibe-seller'  # re-added below
            }
            active['vibe-seller'] = {
                'command': sys.executable,
                'args': [
                    '-m',
                    'app.mcp_server',
                    '--token',
                    token,
                    '--port',
                    str(BACKEND_PORT),
                    '--task-id',
                    self.task_id,
                ],
                'cwd': str(BASE_DIR),
            }
            mcp_config['mcpServers'] = active
            atomic_write_json(ws_dir / '.mcp.json', mcp_config)
        except Exception:
            logger.exception('Failed to register vibe-seller MCP')
            # Fallback: write empty config so --strict-mcp-config
            # still blocks global servers.
            try:
                fallback = ws_dir / '.mcp.json'
                if not fallback.exists():
                    atomic_write_json(fallback, {'mcpServers': {}})
            except Exception:
                logger.exception('Fallback .mcp.json write also failed')

    async def _send_stdin(self, msg: dict, *, label: str = 'stdin'):
        """Write a JSON message to the subprocess stdin."""
        if not self._proc or not self._proc.stdin:
            return
        line = json.dumps(msg, ensure_ascii=False) + '\n'
        if AGENT_DEBUG:
            logger.info(
                'AGENT_DEBUG [%s] %s=%s',
                self.task_id[:8],
                label,
                line[:2000],
            )
        try:
            self._proc.stdin.write(line.encode('utf-8'))
            await self._proc.stdin.drain()
        except (
            BrokenPipeError,
            ConnectionResetError,
            OSError,
            RuntimeError,
        ):
            pass

    async def _send_interrupt(self):
        """Send an SDK interrupt control request.

        If the CLI doesn't support this control request it will be
        silently ignored and the timeout cascade in stop() handles it.
        """
        await self._send_stdin({
            'type': 'control_request',
            'request_id': str(uuid.uuid4()),
            'request': {'subtype': 'interrupt'},
        })

    async def _force_kill(self):
        """Escalate signals: SIGINT → SIGTERM → SIGKILL.

        We spawned `claude` with ``start_new_session=True``, so its
        PID is the leader of its own POSIX process group. Signal the
        whole group via ``os.killpg`` so claude AND every descendant
        (MCP server, tool subprocesses) tear down atomically — if we
        only signalled the leader, an MCP server could survive long
        enough to race the next agent's startup. ``os.killpg`` is
        POSIX-only; on platforms without it (Windows) we fall back to
        signalling just the leader.

        On Windows there is no process group to signal and
        ``send_signal(SIGINT)`` raises ``ValueError`` for a
        non-console subprocess, so we skip the SIGINT/SIGTERM phase
        and go straight to ``kill()`` (TerminateProcess) below.
        """
        if not self._proc or self._proc.returncode is not None:
            return
        pid = self._proc.pid
        use_killpg = hasattr(os, 'killpg')
        escalation = [] if IS_WINDOWS else [signal.SIGINT, signal.SIGTERM]
        for sig in escalation:
            try:
                if use_killpg:
                    os.killpg(pid, sig)
                else:
                    self._proc.send_signal(sig)
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(
                    self._proc.wait(), timeout=SIGNAL_TIMEOUT
                )
                return
            except TimeoutError:
                continue
        # Final escalation: SIGKILL the group; on Windows (no process
        # group) taskkill_tree kills the whole tree so the MCP server +
        # skill_cli.daemon + browser-use children die too — killing only
        # the leader would orphan the browser daemon, which then keeps
        # driving browser/start on the shared Ziniao.
        try:
            if use_killpg:
                os.killpg(pid, signal.SIGKILL)
            elif IS_WINDOWS:
                await taskkill_tree(pid, timeout=SIGNAL_TIMEOUT)
            else:
                self._proc.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=SIGNAL_TIMEOUT)
        except TimeoutError:
            pass

    async def stop(self):
        """Gracefully interrupt the running agent.

        Sequence: SDK interrupt → wait for Result → signal escalation.
        """
        if self._stopping:
            return
        # Snapshot pending questions BEFORE unblocking events —
        # the hook handler will `.pop()` each request from
        # `_pending_questions` as soon as the event fires.
        if self._pending_questions:
            self._last_pending_questions = dict(self._pending_questions)
        # No process or already exited — just unblock waiters
        if not self._proc or self._proc.returncode is not None:
            for evt in self._answer_events.values():
                evt.set()
            self._plan_approval_event.set()
            # Defensive: if `_stream_output` ran, its finally already
            # set this. If start() died before the reader was even
            # spawned, this prevents any `_wait_for_session_end`
            # waiter from hanging on the unset event.
            self.done.set()
            return

        self._stopping = True

        # Unblock any pending events so handler coroutines exit
        for evt in self._answer_events.values():
            evt.set()
        self._plan_approval_event.set()

        # Ask Claude to finish gracefully
        await self._send_interrupt()

        # Wait for _task (which reads Claude's final Result)
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._task),
                    timeout=INTERRUPT_TIMEOUT,
                )
                return  # clean exit
            except TimeoutError:
                pass  # escalate
            except Exception:
                logger.warning(
                    '[%s] Error in agent task during shutdown',
                    self.task_id[:8],
                    exc_info=True,
                )

        # Signal escalation
        await self._force_kill()

        # Give _task time to drain final output
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._task),
                    timeout=DRAIN_TIMEOUT,
                )
            except TimeoutError:
                pass
            except Exception:
                logger.warning(
                    '[%s] Error draining agent output',
                    self.task_id[:8],
                    exc_info=True,
                )

        # Last resort: cancel reader tasks
        for t in [self._task, getattr(self, '_stderr_task', None)]:
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        # Kill orphaned browser-use daemons for this task
        await self._cleanup_browser_daemons()

    async def submit_answer(self, request_id: str, answers: dict):
        """Submit an answer for a pending AskUserQuestion."""
        self._answers[request_id] = answers
        evt = self._answer_events.get(request_id)
        if evt:
            evt.set()

    async def send_user_message(self, message: str):
        """Send a follow-up user message to the running agent."""
        if self._stopping:
            return
        await self._send_stdin({
            'type': 'user',
            'message': {
                'role': 'user',
                'content': message,
            },
        })

    async def approve_plan(self):
        """Approve the pending plan — agent continues executing."""
        self._plan_approved = True
        # Reset so a subsequent `_wait_for_session_end` call (e.g. by
        # `execute_planned_task`) waits on `done` only and isn't
        # short-circuited by the stale plan-saved signal.
        self.plan_saved_event.clear()
        self._plan_approval_event.set()

    async def reject_plan(self, feedback: str = ''):
        """Reject the pending plan — agent re-plans."""
        self._rejection_feedback = feedback
        self._plan_approved = False
        # Cleared for the same reason as approve_plan: the next plan
        # save will re-set it cleanly.
        self.plan_saved_event.clear()
        self._plan_approval_event.set()

    @property
    def running(self) -> bool:
        if self._proc is None or self._proc.returncode is not None:
            return False
        # Once we've closed stdin (post-result, end-of-stream) or
        # entered ``stop()``, the process is alive but no longer
        # input-capable.  Treat the session as not-running so
        # ``send_user_message`` callers fall through to the resume
        # path instead of writing into a dying pipe.
        return not (self._input_closed or self._stopping)

    async def _is_scheduled_task(self) -> bool:
        """True if this task was spawned by a schedule."""
        try:
            async with async_session() as db:
                task = await db.get(Task, self.task_id)
                return bool(task and task.schedule_id)
        except Exception as e:
            logger.error(
                'Failed to check schedule_id for task %s: %s',
                self.task_id,
                e,
            )
            return False
