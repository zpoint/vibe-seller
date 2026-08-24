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

    # Backend log rotation (app/logging_setup.py). Rolls on size OR age,
    # whichever comes first; at most BACKUP_COUNT old files are kept, so
    # the on-disk ceiling is MAX_BYTES * (BACKUP_COUNT + 1). Defaults:
    # 5 GiB / 7 days / 1 backup → never more than 10 GiB or 2 weeks.
    # Both MAX_BYTES and ROTATE_DAYS at 0 disables rotation.
    LOG_MAX_BYTES = ('LOG_MAX_BYTES', str(5 * 1024**3))
    LOG_ROTATE_DAYS = ('LOG_ROTATE_DAYS', '7')
    LOG_BACKUP_COUNT = ('LOG_BACKUP_COUNT', '1')

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

    # Wall clock a review gate may spend re-driving the agent before it
    # fails open with the UNVERIFIED banner. Counted from the FIRST
    # re-drive, alongside (not instead of) the attempt cap in
    # app/ai/claude_backend_utils.py. Exists because five attempts at
    # 100-150s/turn outlast any budget a caller allows a run, so the
    # gate could consume the whole clock and have the run killed while
    # holding a finished deliverable. 0 = attempts only (old behavior).
    # See app/ai/review_redrive.py.
    REVIEW_REDRIVE_BUDGET_S = ('VIBE_REVIEW_REDRIVE_BUDGET_S', '300')

    # Browser lifecycle: terminate a store's browser (main/aux/web)
    # when no active task is bound to it AND its CDP mux has been
    # idle this long (0 = never). TAB_CAP bounds how many tabs one
    # client (task) may keep open — the oldest is closed beyond it
    # (0 = unbounded). See app/browser/idle_sweep.py.
    BROWSER_IDLE_S = ('VIBE_BROWSER_IDLE_S', '300')
    TAB_CAP = ('VIBE_TAB_CAP', '12')

    # Parallel worker slots per task on a store's (or the web) browser.
    # `browser-use --worker N` hands the caller its OWN daemon AND its
    # own CDP mux client, so a subagent driving the browser at the same
    # time as its parent gets a separate tab instead of interleaving on
    # the parent's. The wrapper bakes this bound in at generation time
    # and validates N server-side; 0 disables the flag entirely.
    # See app/browser/wrapper.py and docs/browser.md § Parallel workers.
    BROWSER_WORKER_SLOTS = ('VIBE_BROWSER_WORKER_SLOTS', '3')

    # Circuit breaker on the dead-mux full-env relaunch in
    # BrowserManager.start_session: at most RELAUNCH_MAX relaunches per
    # store within RELAUNCH_WINDOW_S, after which the start fails with
    # an actionable error instead of restarting forever (0 = unbounded).
    # A wedged Ziniao client otherwise turns every browser-use call
    # into another stop/start cycle. See app/browser/manager.py.
    BROWSER_RELAUNCH_MAX = ('VIBE_BROWSER_RELAUNCH_MAX', '3')
    BROWSER_RELAUNCH_WINDOW_S = ('VIBE_BROWSER_RELAUNCH_WINDOW_S', '600')

    # Hard ceiling on one store's browser launch. start_session holds a
    # GLOBAL lock (deliberate — it serializes Ziniao startBrowser so the
    # shared client isn't hammered concurrently), so an unbounded
    # per-store retry loop starves every OTHER store's launch. Cap it so
    # a broken store fails fast instead of taking the machine with it
    # (0 = unbounded). See app/browser/manager.py.
    BROWSER_START_TIMEOUT_S = ('VIBE_BROWSER_START_TIMEOUT_S', '180')

    # How long a caller may WAIT for the manager's global launch lock
    # before being told the subsystem is busy. Without a ceiling here a
    # wedged holder makes every later `POST /browser/start` hang with no
    # response at all — the observed failure was a 240 s curl returning
    # HTTP 000, which the agent can only read as "the machine is
    # broken". Deliberately SHORTER than the wrapper's own 90 s curl so
    # the caller gets a real 503 it can act on instead of a timeout, and
    # shorter than BROWSER_START_TIMEOUT_S so a peer's legitimate launch
    # is reported as "busy, retry" rather than waited out.
    BROWSER_LOCK_WAIT_S = ('VIBE_BROWSER_LOCK_WAIT_S', '60')

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
