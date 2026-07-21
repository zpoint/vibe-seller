"""Centralized environment variable options for vibe-seller.

Follows the SkyPilot pattern (sky/utils/env_options.py): each enum
member is an (env_var_name, default_value) pair with typed accessors.
"""

import enum
import os


class Options(enum.Enum):
    """Environment variables for Vibe Seller.

    Each member is ``(env_var_name, default_value)``.
    Use ``.get()`` for strings, ``.get_bool()`` for booleans,
    ``.get_int(fallback)`` for integers.
    """

    # Auth
    ADMIN_USERNAME = ('ADMIN_USERNAME', 'admin')
    ADMIN_EMAIL = ('ADMIN_EMAIL', 'admin@vibe-seller.local')
    ADMIN_PASSWORD = ('ADMIN_PASSWORD', 'admin')
    JWT_SECRET = (
        'JWT_SECRET',
        'change-me-in-production-use-a-long-secret-key',
    )
    AUTH_REQUIRED = ('VIBE_AUTH_REQUIRED', 'false')
    FORCE_ADMIN_RESET = ('FORCE_ADMIN_RESET', 'false')

    # Server
    BACKEND_PORT = ('BACKEND_PORT', '7777')
    HOST = ('HOST', '0.0.0.0')
    FRONTEND_URL = ('FRONTEND_URL', '')
    LOG_DIR = ('LOG_DIR', '')

    # Logging
    LOG_LEVEL = ('LOG_LEVEL', 'INFO')

    # AI Agent
    AGENT_DEBUG = ('AGENT_DEBUG', 'false')
    MOCK_CLI = ('MOCK_CLI', '')
    ANTHROPIC_MODEL = ('ANTHROPIC_MODEL', '')
    MAX_AGENT_CONCURRENCY = ('MAX_AGENT_CONCURRENCY', '2')
    ANTHROPIC_API_KEY = ('ANTHROPIC_API_KEY', '')
    MAX_REPEAT_TOOL_CALLS = ('VIBE_MAX_REPEAT_TOOL_CALLS', '6')

    # Turn lifecycle: how long a quiescent CLI process may linger
    # after its turn's result before stdin is closed to end it.
    # 0 = close at the result event (legacy behavior). The async
    # tier applies when background subagents were launched this
    # process (late notifications and NESTED spawns are invisible
    # to tracking and need the grace); the quiet tier otherwise.
    # HARD_IDLE closes a process that emits NO stream events at all
    # for that long, regardless of gates (0 = disabled). See
    # app/ai/claude_backend_turns.py.
    TURN_LINGER_S = ('VIBE_TURN_LINGER_S', '60')
    TURN_LINGER_QUIET_S = ('VIBE_TURN_LINGER_QUIET_S', '5')
    TURN_HARD_IDLE_S = ('VIBE_TURN_HARD_IDLE_S', '600')

    # Browser lifecycle: terminate a store's browser (main/aux/web)
    # when no active task is bound to it AND its CDP mux has been
    # idle this long (0 = never). TAB_CAP bounds how many tabs one
    # client (task) may keep open — the oldest is closed beyond it
    # (0 = unbounded). See app/browser/idle_sweep.py.
    BROWSER_IDLE_S = ('VIBE_BROWSER_IDLE_S', '300')
    TAB_CAP = ('VIBE_TAB_CAP', '12')

    # Sync
    KNOWLEDGE_REPO_URL = ('KNOWLEDGE_REPO_URL', '')
    SKILLS_REPO_URL = ('SKILLS_REPO_URL', '')

    def __init__(self, env_var: str, default: str) -> None:
        super().__init__()
        self.env_var = env_var
        self.default = default

    def __repr__(self) -> str:
        return self.env_var

    def get(self) -> str:
        """Return the env var value, or its default."""
        return os.environ.get(self.env_var, self.default)

    def get_bool(self) -> bool:
        """Return ``True`` if the env var is ``'true'`` or ``'1'``."""
        return self.get().lower() in ('true', '1')

    def get_int(self) -> int:
        """Return the env var as an int, or the default on failure."""
        try:
            return int(self.get())
        except ValueError:
            return int(self.default)

    def get_float(self) -> float:
        """Return the env var as a float, or the default on failure."""
        try:
            return float(self.get())
        except ValueError:
            return float(self.default)
