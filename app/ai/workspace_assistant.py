"""Workspace AI assistant — ephemeral chat for organizing knowledge.

Reuses AgentSession with overrides for:
- SSE-only messaging (no DB persistence)
- Multi-turn subprocess (stdin kept open across result events — each
  turn ends with its own ``ws_assistant_turn_done`` event; the process
  end is ``ws_assistant_done``)
- Separate concurrency from task agents
"""

import asyncio
from collections.abc import Callable
import json
import logging
import os
import sys

from app.ai.agent_proxy import (
    drop_dead_proxy,
    launch_settings,
)
from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import (
    mcp_launch_flags,
    resolve_claude_binary,
)
from app.ai.launch_artifacts import build_launch_artifacts
from app.ai.profiles import DEFAULT_PROFILE_ID, ProfileManager
from app.auth import create_token
from app.config import AI_BOT_USER_ID, BACKEND_PORT, BASE_DIR
from app.env_options import Options
from app.events.bus import event_bus
from app.platform import prepend_to_path, venv_bin_dir
from app.workspace.manager import VIBE_SELLER_DIR, workspace_manager

logger = logging.getLogger(__name__)

# The workspace assistant may manage SKILLS, nothing task-only. It
# launches the same stdio MCP the task agents run, restricted to this
# allowlist (app.mcp_server --tools): skill writes then go through
# the skills-manager guards instead of hand-written files — hand-
# writing was the bug class (the UI's 「让助手从URL安装」 promise had
# no code path: the assistant could only curl and Write, bypassing
# slug rules, size caps, symlink refusal, the overwrite gate and the
# lockfile provenance). save_skill stays text authoring; import_skill
# installs whole directories/URLs byte-for-byte.
ASSISTANT_SKILL_TOOLS = ','.join((
    'vibe_seller_list_skills',
    'vibe_seller_save_skill',
    'vibe_seller_import_skill',
))


class WorkspaceAgentSession(AgentSession):
    """Agent session for the workspace assistant.

    Key differences from task sessions:
    - Messages emitted via ``ws_assistant_*`` SSE events, not persisted
    - ``result`` events do NOT close stdin (multi-turn) — each turn
      ends with its own ``ws_assistant_turn_done`` event
    - No design plan / result / todo persistence
    """

    # True from the delivery of a user message until its ``result``
    # arrives (or the process ends): the turn is IN FLIGHT. This — not
    # process liveness — is what ``status.running`` reports (the UI's
    # composer and the MCP tool read it as "busy"); the inherited
    # ``running`` property stays the liveness fact and surfaces as
    # ``status.alive``. Class attribute so every construction path
    # reads False, never AttributeError.
    turn_in_flight: bool = False

    def __init__(
        self,
        session_key: str,
        prompt: str,
        system_prompt_extra: str = '',
        profile_id: str = DEFAULT_PROFILE_ID,
        message_history: list[dict] | None = None,
        on_result: Callable[[str], None] | None = None,
    ):
        super().__init__(
            task_id=session_key,
            prompt=prompt,
            system_prompt_extra=system_prompt_extra,
            mode='workspace',
            profile_id=profile_id,
            message_history=message_history or [],
            no_store=False,
        )
        # Fired with each `result` text, so the manager can keep the
        # user's LAST reply across sessions (a later session that
        # errors before answering must not erase it). No DB exists for
        # the assistant — this callback is the persistence.
        self._on_result = on_result

    def _report_result(self, text: str) -> None:
        if self._on_result is not None:
            self._on_result(text)

    # ── Overrides ────────────────────────────────────────

    async def _emit_message(self, role: str, content: str):
        """SSE-only — no DB write."""
        await event_bus.emit(
            'ws_assistant_message',
            {
                'session_key': self.task_id,
                'role': role,
                'content': content,
            },
        )

    async def _handle_event(self, event: dict):
        """Same as parent but keep stdin open on result, and end the
        turn with ``ws_assistant_turn_done``."""
        etype = event.get('type', '')

        if etype == 'assistant':
            content = event.get('message', {}).get('content', [])
            for block in content:
                if block.get('type') == 'text':
                    await self._emit_message('assistant', block['text'])
                elif block.get('type') == 'tool_use':
                    tool_name = block.get('name', '')
                    tool_input = block.get('input', {})
                    # Emit tool_use (skip TodoWrite SSE/persist)
                    await self._emit_message(
                        'tool_use',
                        json.dumps(
                            {'tool': tool_name, 'input': tool_input},
                            ensure_ascii=False,
                        ),
                    )

        elif etype == 'result':
            # Emit content but do NOT close stdin
            text = event.get('result', '')
            if text:
                self._result_text = text
                self._report_result(text)
                await self._emit_message('result', text)
            # Turn end ≠ process end. One CLI process hosts every turn
            # of the session (stdin stays open), so ``ws_assistant_done``
            # fires only when the process exits — a consumer waiting on
            # it for THIS turn's completion never wakes while the
            # session is healthy. ``ws_assistant_turn_done`` is the
            # per-turn signal: the UI clears its running state on it,
            # and the hosted-MCP collector ends its send-and-wait on it.
            # Emitted for EVERY result, empty ones included: the turn
            # is over either way and the running state must clear.
            # Busy closes BEFORE the event goes out, so a consumer
            # waking on it reads a consistent ``status`` (not running).
            self.turn_in_flight = False
            await event_bus.emit(
                'ws_assistant_turn_done',
                {'session_key': self.task_id},
            )

        elif etype == 'content_block_delta':
            delta = event.get('delta', {})
            if delta.get('type') == 'text_delta':
                await self._emit_message('delta', delta.get('text', ''))
        else:
            await self._emit_message(
                'agent_event',
                json.dumps(event, ensure_ascii=False),
            )

    async def _handle_control_request(self, msg: dict):
        """Auto-allow all control requests.

        Unlike task sessions, the workspace assistant has no
        answer endpoint — so AskUserQuestion is auto-allowed
        to avoid deadlocking the session.
        """
        request_id = msg.get('request_id', '')
        await self._send_control_response(request_id, 'allow')

    async def send_user_message(self, message: str) -> bool:
        """Deliver a follow-up message; a DELIVERED message opens the
        turn — busy until its ``result`` ends it."""
        delivered = await super().send_user_message(message)
        if delivered:
            self.turn_in_flight = True
        return delivered

    async def stop(self):
        """A stopped session is never busy: clear the in-flight turn
        up front. The base's interrupt/kill cascade ends the stream
        loop, which clears it anyway — but ``manager.stop()`` callers
        read ``status`` immediately after and must not see a stale
        turn."""
        self.turn_in_flight = False
        await super().stop()

    async def _emit_process_done(self, **extra):
        """Declare PROCESS end (stop / crash / stdin EOF) — the ONE
        place ``ws_assistant_done`` is emitted. An in-flight turn can
        never outlive its process, so busy closes here too; per-turn
        completion is ``ws_assistant_turn_done`` instead."""
        self.turn_in_flight = False
        await event_bus.emit(
            'ws_assistant_done',
            {'session_key': self.task_id, **extra},
        )

    async def _stream_output(self):
        """Same as parent but emit ws_assistant_done (PROCESS end —
        per-turn completion is ``ws_assistant_turn_done``), skip saves."""
        try:
            while self._proc and self._proc.stdout:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                text = line.decode('utf-8', errors='replace').strip()
                if not text:
                    continue

                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    await self._emit_message('assistant', text)
                    continue

                if event.get('type') == 'control_request':
                    await self._handle_control_request(event)
                else:
                    await self._handle_event(event)

            # stdout closed — close stdin through the shared path and
            # wait BOUNDED: a CLI stalling after its final result must
            # be escalated, not awaited forever (same contract as
            # AgentSession's stream loop — see
            # _wait_process_exit_after_close).
            await self._close_stdin('stdout_eof', emit=False)
            return_code = await self._wait_process_exit_after_close()

            await self._apply_exit_code(return_code)

            await self._emit_process_done(return_code=return_code)

        except asyncio.CancelledError:
            await self._emit_process_done(
                return_code=-1,
                interrupted=True,
            )
        except Exception as e:
            logger.exception(
                'WS assistant stream error for %s: %s',
                self.task_id,
                e,
            )
            await self._emit_message('system', f'Agent error: {e}')
            await self._emit_process_done(return_code=-1)

    async def _persist_todos(self, todos: list[dict]):
        """No-op — no task record."""

    async def start(self):
        """Spawn claude with workspace mode."""
        await workspace_manager.ensure_init()

        mock_cli = Options.MOCK_CLI.get()
        if mock_cli:
            # Test mode: the same mock CLI the task agent runs under.
            # The assistant is an agent pipeline too, and MOCK_CLI is
            # what lets CI exercise this turn loop without model
            # credits. '--mode=workspace' selects the mock's own
            # multi-turn flow — one process across turns, exit only on
            # stdin EOF — because a mock that exits after each result
            # would hide the real CLI's live-session lifecycle (the
            # per-turn-completion bug class).
            cmd = [
                sys.executable,
                os.path.abspath(mock_cli),
                '--mode=workspace',
            ]
        else:
            # The same binary task agents spawn: the project-local
            # install first, PATH only as the fallback. A bare 'claude'
            # failed to start wherever the CLI is not on PATH.
            cmd = [
                resolve_claude_binary(),
                '-p',
                '--output-format',
                'stream-json',
                '--input-format',
                'stream-json',
                '--verbose',
                '--permission-mode',
                'bypassPermissions',
                '--permission-prompt-tool',
                'stdio',
            ]

        ws_dir = VIBE_SELLER_DIR
        if ws_dir.exists():
            cmd.extend(['--add-dir', str(ws_dir)])

        # Block global browser MCP tools
        cmd.extend([
            '--disallowedTools',
            'mcp__playwright__*,mcp__browser-use__*',
        ])

        logger.info(
            'Starting workspace assistant %s (profile=%s)',
            self.task_id,
            self.profile_id,
        )

        env = ProfileManager.get_env_for_profile(self.profile_id)
        # ONE --settings file per launch (auto-mode classifier off, see
        # app.ai.agent_proxy.launch_settings), dead-proxy blanks merged
        # into the same payload.
        settings = launch_settings()
        # A proxy that refuses connections fails every model call (#168).
        await drop_dead_proxy(env, self.task_id, settings, self._emit_message)
        # The launch artifacts (system-prompt file + --settings payload)
        # are written in ONE thread hop — start() is async and the
        # event loop must never touch the disk. By file: the store list
        # grows with the account, and the Windows command line is
        # capped (see build_launch_artifacts).
        # Skill tools over the SAME stdio MCP the task agents run,
        # restricted by --tools (module constant). NEVER the shared
        # workspace-root .mcp.json: store-less tasks write that file
        # with their full task toolset and their own task id — two
        # writers, one file. Each session owns its per-launch config
        # instead. Whether it runs STRICT follows the same profile
        # rule the task launches use (one helper, one decision): a
        # profile that loads global MCP servers gets the per-session
        # file merged in WITHOUT --strict-mcp-config; the default
        # (globals off) gets the strict pair.
        mcp_config = None
        if not mock_cli:
            mcp_config = {
                'mcpServers': {
                    'vibe-seller': {
                        'command': sys.executable,
                        'args': [
                            '-m',
                            'app.mcp_server',
                            '--token',
                            create_token(AI_BOT_USER_ID, 'ai_bot'),
                            '--port',
                            str(BACKEND_PORT),
                            '--tools',
                            ASSISTANT_SKILL_TOOLS,
                        ],
                        'cwd': str(BASE_DIR),
                    }
                }
            }
        artifacts = await asyncio.to_thread(
            build_launch_artifacts,
            self.task_id,
            None,
            self.system_prompt_extra or '',
            settings,
            mcp_config=mcp_config,
        )
        if artifacts['system_prompt'] is not None:
            cmd.extend([
                '--append-system-prompt-file',
                str(artifacts['system_prompt']),
            ])
        cmd.extend(['--settings', str(artifacts['settings'])])
        cmd.extend(
            mcp_launch_flags(
                load_global=ProfileManager.get_load_global_mcp(self.profile_id),
                session_config=artifacts['mcp_config'],
                project_config=None,
            )
        )
        venv_bin = venv_bin_dir(VIBE_SELLER_DIR / '.venv')
        if venv_bin.is_dir():
            prepend_to_path(env, venv_bin)
            env['VIRTUAL_ENV'] = str(VIBE_SELLER_DIR / '.venv')

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=100 * 1024 * 1024,
            env=env,
            cwd=str(ws_dir) if ws_dir.exists() else None,
        )

        messages = []
        for msg in self.message_history:
            if msg.get('role') in ('user', 'assistant'):
                messages.append({
                    'role': msg['role'],
                    'content': msg['content'],
                })
        messages.append({
            'role': 'user',
            'content': self.prompt,
        })

        delivered = False
        for msg in messages:
            delivered = await self._send_stdin({
                'type': 'user',
                'message': msg,
            })
        # The prompt (the last message) reached a live pipe: the
        # assistant is BUSY with its first turn from here until that
        # turn's result. A dead pipe leaves busy False — the stream
        # loop's process-end path is the backstop either way.
        self.turn_in_flight = delivered

        self._stderr_task = asyncio.create_task(self._stream_stderr())
        self._task = asyncio.create_task(self._stream_output())


class WorkspaceAssistantManager:
    """Manages workspace assistant sessions per user."""

    MAX_CONCURRENT = 2

    def __init__(self):
        self._sessions: dict[str, WorkspaceAgentSession] = {}
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)
        self._message_history: dict[str, list[dict]] = {}
        # The last result text each user's assistant produced, kept by
        # the sessions' ``on_result`` callbacks — a session that errors
        # before answering must not erase the previous answer.
        self._last_replies: dict[str, str] = {}
        # One in-flight TOOL send per user (see ``send_lock``).
        self._send_locks: dict[str, asyncio.Lock] = {}

    def _session_key(self, user_id: str) -> str:
        return f'__ws_assistant_{user_id}__'

    def send_lock(self, user_id: str) -> asyncio.Lock:
        """The per-user serializer for MCP-driven assistant sends.

        The assistant's reply arrives on a live-only event stream
        keyed by the USER (no per-turn id), so two concurrent
        ``workspace_assistant(send)`` calls would each collect the
        other turn's messages and done event — replies crossed. The
        hosted-MCP tool holds this lock for the whole send-and-wait;
        a caller that finds it held is told to wait (see
        ``app/agent_mcp/tools_assistant.py``). The web UI is
        unaffected: it renders the stream, it does not collect a
        turn.
        """
        key = self._session_key(user_id)
        lock = self._send_locks.get(key)
        if lock is None:
            lock = self._send_locks[key] = asyncio.Lock()
        return lock

    async def send_or_start(
        self,
        user_id: str,
        message: str,
        profile_id: str = DEFAULT_PROFILE_ID,
        system_prompt: str = '',
    ) -> bool:
        """Send message to running session, or start new one."""
        key = self._session_key(user_id)
        self._message_history.setdefault(key, []).append({
            'role': 'user',
            'content': message,
        })
        session = self._sessions.get(key)
        if session and session.running:
            await session.send_user_message(message)
            return True
        return await self._start(key, message, profile_id, system_prompt)

    async def _start(
        self,
        key: str,
        message: str,
        profile_id: str,
        system_prompt: str,
    ) -> bool:
        """Start a new workspace assistant session."""
        await self._semaphore.acquire()
        try:
            # Build message history (exclude last, it's the prompt)
            history = self._message_history.get(key, [])[:-1]

            def _keep_reply(text: str, _key=key) -> None:
                self._last_replies[_key] = text

            session = WorkspaceAgentSession(
                session_key=key,
                prompt=message,
                system_prompt_extra=system_prompt,
                profile_id=profile_id,
                message_history=history,
                on_result=_keep_reply,
            )
            self._sessions[key] = session
            await session.start()
        except Exception:
            self._semaphore.release()
            raise

        async def _release_on_done():
            if session._task:
                try:
                    await session._task
                except (asyncio.CancelledError, Exception):
                    pass
            self._semaphore.release()

        asyncio.create_task(_release_on_done())
        return True

    async def stop(self, user_id: str) -> bool:
        """Stop the workspace assistant for a user."""
        key = self._session_key(user_id)
        session = self._sessions.get(key)
        if not session or not session.running:
            return False
        await session.stop()
        return True

    def is_running(self, user_id: str) -> bool:
        """BUSY with a turn: a delivered message awaits its result.

        False once the reply is in — even though the session's process
        stays ALIVE across turns (that separate fact is ``alive``).
        Reading liveness as busy is what left the UI's composer on
        Stop for the whole live session; ``status.running`` (route +
        MCP tool) reports THIS.
        """
        key = self._session_key(user_id)
        session = self._sessions.get(key)
        return bool(session and session.turn_in_flight)

    def alive(self, user_id: str) -> bool:
        """The session's process is up and input-capable — the next
        send reuses it (context and all). Routinely True while
        ``is_running`` is False (idle between turns)."""
        key = self._session_key(user_id)
        session = self._sessions.get(key)
        return session.running if session else False

    def session_key(self, user_id: str) -> str:
        """The per-user session key (public form).

        The assistant's output is SSE-only (``ws_assistant_*`` events
        keyed by it); the hosted MCP tool filters the same events by
        this key, so it must be the ONE definition.
        """
        return self._session_key(user_id)

    def last_reply(self, user_id: str) -> str | None:
        """The last result text this user's assistant produced, if any.

        Nothing is persisted to the DB for the assistant — the
        ``on_result`` callbacks into ``_last_replies`` are the only
        record, so the answer SURVIVES the session that produced it
        (a later session that errors before answering must not erase
        it). ``None`` when the user's assistant never answered.
        """
        return self._last_replies.get(self._session_key(user_id))

    def clear_history(self, user_id: str):
        """Clear message history for a user (and their last reply)."""
        key = self._session_key(user_id)
        self._message_history.pop(key, None)
        self._last_replies.pop(key, None)


# Singleton
ws_assistant_manager = WorkspaceAssistantManager()
