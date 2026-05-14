"""Workspace AI assistant — ephemeral chat for organizing knowledge.

Reuses AgentSession with overrides for:
- SSE-only messaging (no DB persistence)
- Multi-turn subprocess (stdin kept open across result events)
- Separate concurrency from task agents
"""

import asyncio
import json
import logging

from app.ai.claude_backend import AgentSession
from app.ai.profiles import DEFAULT_PROFILE_ID, ProfileManager
from app.events.bus import event_bus
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

        system_prompt = self.system_prompt_extra or ''
        if system_prompt.strip():
            cmd.extend(['--append-system-prompt', system_prompt])

        logger.info(
            'Starting workspace assistant %s (profile=%s)',
            self.task_id,
            self.profile_id,
        )

        env = ProfileManager.get_env_for_profile(self.profile_id)
        venv_bin = VIBE_SELLER_DIR / '.venv' / 'bin'
        if venv_bin.is_dir():
            env['PATH'] = f'{venv_bin}:{env.get("PATH", "")}'
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

    def _session_key(self, user_id: str) -> str:
        return f'__ws_assistant_{user_id}__'

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
            session = WorkspaceAgentSession(
                session_key=key,
                prompt=message,
                system_prompt_extra=system_prompt,
                profile_id=profile_id,
                message_history=history,
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

    def clear_history(self, user_id: str):
        """Clear message history for a user."""
        key = self._session_key(user_id)
        self._message_history.pop(key, None)


# Singleton
ws_assistant_manager = WorkspaceAssistantManager()
