"""Workspace AI assistant — ephemeral chat for organizing knowledge.

Reuses AgentSession with overrides for:
- SSE-only messaging (no DB persistence)
- Multi-turn subprocess (stdin kept open across result events)
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
from app.ai.profiles import DEFAULT_PROFILE_ID, ProfileManager
from app.env_options import Options
from app.events.bus import event_bus
from app.platform import prepend_to_path, venv_bin_dir
from app.workspace.manager import VIBE_SELLER_DIR, workspace_manager

logger = logging.getLogger(__name__)


class WorkspaceAgentSession(AgentSession):
    """Agent session for the workspace assistant.

    Key differences from task sessions:
    - Messages emitted via ``ws_assistant_*`` SSE events, not persisted
    - ``result`` events do NOT close stdin (multi-turn)
    - No design plan / result / todo persistence
    """

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
        """Same as parent but keep stdin open on result."""
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

    async def _stream_output(self):
        """Same as parent but emit ws_assistant_done, skip saves."""
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

            # stdout closed — close stdin
            if self._proc and self._proc.stdin:
                try:
                    self._proc.stdin.close()
                except Exception:
                    pass

            return_code = await self._proc.wait() if self._proc else -1

            if return_code != 0:
                await self._emit_message(
                    'system',
                    f'Agent exited with code {return_code}',
                )

            await event_bus.emit(
                'ws_assistant_done',
                {
                    'session_key': self.task_id,
                    'return_code': return_code,
                },
            )

        except asyncio.CancelledError:
            await event_bus.emit(
                'ws_assistant_done',
                {
                    'session_key': self.task_id,
                    'return_code': -1,
                    'interrupted': True,
                },
            )
        except Exception as e:
            logger.exception(
                'WS assistant stream error for %s: %s',
                self.task_id,
                e,
            )
            await self._emit_message('system', f'Agent error: {e}')
            await event_bus.emit(
                'ws_assistant_done',
                {
                    'session_key': self.task_id,
                    'return_code': -1,
                },
            )

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
            # credits. '--mode=workspace' maps the mock to its execute
            # flow (it has no plan gate — control requests are
            # auto-allowed below).
            cmd = [
                sys.executable,
                os.path.abspath(mock_cli),
                '--mode=workspace',
            ]
        else:
            cmd = [
                'claude',
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
        artifacts = await asyncio.to_thread(
            self.task_id,
            None,
            self.system_prompt_extra or '',
            settings,
            agents=False,
        )
        if artifacts['system_prompt'] is not None:
            cmd.extend([
                '--append-system-prompt-file',
                str(artifacts['system_prompt']),
            ])
        cmd.extend(['--settings', str(artifacts['settings'])])
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

        for msg in messages:
            await self._send_stdin({
                'type': 'user',
                'message': msg,
            })

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
