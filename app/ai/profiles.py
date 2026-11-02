"""AI Profile management for chat-level agent switching."""

import json
import os

from app.ai.provider_presets import PROVIDER_MODELS, PROVIDER_PRESETS
from app.config import AI_BOT_ROLE, DEFAULT_USER_ID
from app.models.user import User
from app.workspace.manager import VIBE_SELLER_DIR

PROFILES_PATH = VIBE_SELLER_DIR / 'profiles.json'

DEFAULT_PROFILE_ID = 'default'

# Per-request API timeout (ms) every agent CLI is spawned with. Every
# provider preset in provider_presets.py ships this value; a custom
# profile (or a bare server env) without it would leave the CLI on its
# own default, and nothing could compute the bound a request actually
# runs under — injected by ``ProfileManager.get_env_for_profile``;
# consumed by the stall reaper's alive-branch
# (``app/scheduler/stall_reaper.py``).
DEFAULT_API_TIMEOUT_MS = 3_000_000

# Stream-idle watchdog bound (ms) injected into every agent spawn
# env, together with the switch that arms it. The Claude Code CLI's
# own watchdog (CLAUDE_ENABLE_STREAM_WATCHDOG +
# CLAUDE_STREAM_IDLE_TIMEOUT_MS; src/services/api/claude.ts) aborts
# an SSE stream from which NO chunk has arrived for this long — the
# timer arms before the first chunk (post-headers TTFB counts) and
# resets on every chunk, so it bounds every silent gap of the
# response BODY. The CLI's own default (90 000) sits below the worst
# silent compose observed from a healthy slow provider (MiniMax-M3:
# 3–4 min after a large tool_result), so it is raised here with
# headroom; anything the CLI still considers dead at 7 min goes to
# its non-streaming fallback → api_retry (visible on stdout).
DEFAULT_STREAM_IDLE_TIMEOUT_MS = 420_000

# The CLI's isEnvTruthy (src/utils/envUtils.ts) — mirrored so the
# reaper and the CLI always agree on whether the watchdog is armed.
_ENV_TRUTHY = {'1', 'true', 'yes', 'on'}


def effective_api_timeout_ms(env: dict | None) -> int:
    """The per-request timeout a CLI spawned with ``env`` will use.

    Missing, unparseable, or non-positive values fall back to
    ``DEFAULT_API_TIMEOUT_MS`` — the same value ``AgentSession.start``
    injects when the env lacks one, so both sides (the CLI and the
    stall reaper) always agree on the bound.
    """
    raw = (env or {}).get('API_TIMEOUT_MS')
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_API_TIMEOUT_MS
    return value if value > 0 else DEFAULT_API_TIMEOUT_MS


def effective_stream_idle_timeout_ms(env: dict | None) -> int:
    """The stream-idle watchdog bound a CLI spawned with ``env`` uses.

    Same parse contract as ``effective_api_timeout_ms``: the value
    ``get_env_for_profile`` injects when the key is missing, so the
    stall reaper's silence bound and the watchdog the CLI actually
    runs under can never disagree.
    """
    raw = (env or {}).get('CLAUDE_STREAM_IDLE_TIMEOUT_MS')
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_STREAM_IDLE_TIMEOUT_MS
    return value if value > 0 else DEFAULT_STREAM_IDLE_TIMEOUT_MS


def stream_watchdog_enabled(env: dict | None) -> bool:
    """Whether the CLI's stream-idle watchdog is armed for ``env``.

    Mirrors the CLI's truthiness check exactly ('0'/'false'/'off'/
    ''/missing ⇒ disarmed), so a profile that disables the watchdog
    also disables the tight reaper bound that relies on it.
    """
    raw = (env or {}).get('CLAUDE_ENABLE_STREAM_WATCHDOG')
    if raw is None:
        return False
    return str(raw).strip().lower() in _ENV_TRUTHY


def ensure_bounded_api_timeout(env: dict) -> dict:
    """Inject ``API_TIMEOUT_MS`` into a spawn env that lacks one.

    An explicit value always wins (operator's choice); only a
    missing key gets the default. Applied by
    ``ProfileManager.get_env_for_profile`` so no CLI ever runs an
    unbounded request — the guarantee the stall reaper's
    alive-branch relies on.
    """
    env.setdefault('API_TIMEOUT_MS', str(DEFAULT_API_TIMEOUT_MS))
    return env


def ensure_stream_liveness_env(env: dict) -> dict:
    """Arm the CLI's own stream liveness machinery in a spawn env.

    Three keys, each ``setdefault`` so an explicit profile value
    always wins:

    - ``CLAUDE_ENABLE_STREAM_WATCHDOG`` — make a silently-dead SSE
      stream LOUD inside the CLI: without it the SDK timeout covers
      only the headers phase and a dropped body hangs the session
      indefinitely (the CLI's own comment).
    - ``CLAUDE_STREAM_IDLE_TIMEOUT_MS`` — the bound above (see
      ``DEFAULT_STREAM_IDLE_TIMEOUT_MS``).
    - ``CLAUDE_CODE_INCLUDE_PARTIAL_MESSAGES`` — forward raw SSE
      parts (message_start / deltas / pings) to stdout. Without it a
      healthy slow compose and a dead stream are BOTH pure stdout
      silence; with it, every part is a stdout line, and the
      backend's any-line heartbeat
      (``claude_backend_stream._stream_output``) feeds
      ``Task.updated_at`` with zero parser changes.

    These are what make "silent but alive" a bounded state the
    stall reaper can reason about (``app/scheduler/stall_reaper.py``).
    """
    env.setdefault('CLAUDE_ENABLE_STREAM_WATCHDOG', '1')
    env.setdefault(
        'CLAUDE_STREAM_IDLE_TIMEOUT_MS', str(DEFAULT_STREAM_IDLE_TIMEOUT_MS)
    )
    env.setdefault('CLAUDE_CODE_INCLUDE_PARTIAL_MESSAGES', '1')
    return env


DEFAULT_PROFILES = {
    'profiles': {
        DEFAULT_PROFILE_ID: {
            'name': 'Claude',
            'description': 'System default configuration',
            'env': {},
            'load_global_mcp': False,
        }
    }
}


def model_sees_images(model_id: str | None) -> bool | None:
    """Whether ``model_id`` can take an image input, per PROVIDER_MODELS.

    ``False`` only for a model the catalog labels text-only, ``None``
    when it is unknown (not listed, or listed without a ``vision``
    label) -- a caller restricts nothing it cannot justify. A ``[1m]``
    style suffix does not change what the model can see, so it is
    ignored on both sides.
    """

    def base(mid: str) -> str:
        return mid.split('[', 1)[0].strip().lower()

    if not model_id or not base(model_id):
        return None
    want = base(model_id)
    for models in PROVIDER_MODELS.values():
        for m in models:
            if base(m.get('id', '')) == want and 'vision' in m:
                return bool(m['vision'])
    return None


class ProfileManager:
    """Manage AI agent profiles stored in profiles.json."""

    @staticmethod
    def load() -> dict:
        """Load profiles from disk or create defaults."""
        if PROFILES_PATH.exists():
            with open(PROFILES_PATH, 'r') as f:
                return json.load(f)
        return DEFAULT_PROFILES.copy()

    @staticmethod
    def save(profiles: dict) -> None:
        """Save profiles to disk."""
        PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PROFILES_PATH, 'w') as f:
            json.dump(profiles, f, indent=2)

    @staticmethod
    def get_profile(profile_id: str) -> dict | None:
        """Get a single profile by ID."""
        profiles = ProfileManager.load()
        return profiles['profiles'].get(profile_id)

    @staticmethod
    def list_profiles() -> list[dict]:
        """List all available profiles."""
        profiles = ProfileManager.load()
        return [{'id': k, **v} for k, v in profiles['profiles'].items()]

    @staticmethod
    def create_profile(
        profile_id: str,
        name: str,
        env: dict,
        description: str = '',
        load_global_mcp: bool = False,
    ) -> dict:
        """Create a new profile and return it with its id."""
        profiles = ProfileManager.load()
        profile_data = {
            'name': name,
            'description': description,
            'env': env,
            'load_global_mcp': load_global_mcp,
        }
        profiles['profiles'][profile_id] = profile_data
        ProfileManager.save(profiles)
        return {'id': profile_id, **profile_data}

    @staticmethod
    def update_profile(profile_id: str, updates: dict) -> dict:
        """Update an existing profile and return it with its id."""
        profiles = ProfileManager.load()
        if profile_id not in profiles['profiles']:
            raise ValueError(f'Profile {profile_id} not found')
        profiles['profiles'][profile_id].update(updates)
        ProfileManager.save(profiles)
        return {
            'id': profile_id,
            **profiles['profiles'][profile_id],
        }

    @staticmethod
    def delete_profile(profile_id: str) -> None:
        """Delete a profile. Cannot delete the default profile."""
        if profile_id == DEFAULT_PROFILE_ID:
            raise ValueError('Cannot delete default profile')
        profiles = ProfileManager.load()
        profiles['profiles'].pop(profile_id, None)
        ProfileManager.save(profiles)

    @staticmethod
    def get_env_for_profile(profile_id: str) -> dict:
        """Get the environment variables for a profile.

        Returns merged system env + profile overrides, with the CLI
        stream guards guaranteed present (``ensure_bounded_api_timeout``
        + ``ensure_stream_liveness_env``): every consumer of "the env
        a profile runs under" — agent spawn (``AgentSession.start``)
        and the stall reaper's silence bound — sees the same values
        the CLI will actually run with.
        """
        profile = ProfileManager.get_profile(profile_id)
        if not profile:
            return ensure_stream_liveness_env(
                ensure_bounded_api_timeout(os.environ.copy())
            )

        env = os.environ.copy()
        env.update(profile.get('env', {}))
        return ensure_stream_liveness_env(ensure_bounded_api_timeout(env))

    @staticmethod
    def get_load_global_mcp(profile_id: str) -> bool:
        """Whether the profile should load global MCP servers."""
        profile = ProfileManager.get_profile(profile_id)
        if not profile:
            return False
        return profile.get('load_global_mcp', False)

    @staticmethod
    def get_provider_presets() -> dict:
        """Return provider presets for the UI, each augmented with its
        ``models`` dropdown options. Builds a fresh dict so the module
        globals are never mutated."""
        return {
            pid: {**preset, 'models': PROVIDER_MODELS.get(pid, [])}
            for pid, preset in PROVIDER_PRESETS.items()
        }


def profile_kind(profile: dict | None) -> str:
    """Return a privacy-safe enum identifying which provider a profile uses.

    Matches the profile's ANTHROPIC_BASE_URL against PROVIDER_PRESETS
    to get the preset id (kimi, minimax, glm, glm_intl, deepseek, qwen,
    qwen_intl, qwen_coding, qwen_token, mimo, mimo_token) or
    default/custom.
    Used by telemetry; never sends the env itself.
    """
    if not profile:
        return 'default'
    env = profile.get('env') or {}
    base_url = (env.get('ANTHROPIC_BASE_URL') or '').strip()
    if not base_url:
        return 'default'
    for preset_id, preset in PROVIDER_PRESETS.items():
        preset_url = preset.get('env', {}).get('ANTHROPIC_BASE_URL', '').strip()
        if preset_url and preset_url == base_url:
            return preset_id
    return 'custom'


def profile_kind_for_id(profile_id: str | None) -> str:
    """Resolve a profile id (UUID or 'default') to its provider kind."""
    if not profile_id or profile_id == DEFAULT_PROFILE_ID:
        return 'default'
    return profile_kind(ProfileManager.get_profile(profile_id))


async def resolve_owner_profile(owner, db) -> str | None:
    """Resolve an owner's AI profile, falling through a bot account.

    A bot account (``role == AI_BOT_ROLE``) is login-less: its
    ``default_profile_id`` is the placeholder ``'default'`` and no
    Settings page can ever change it. Inheriting from it is therefore
    not a configuration but a guaranteed fall-back to plain Claude on
    api.anthropic.com — and on an install driven entirely by
    third-party profiles, a guaranteed failure. **A bot owner means
    "no preference expressed", so fall through to the human admin who
    did express one.**

    Returns ``None`` when nobody expressed a preference, leaving the
    caller to apply its own default.
    """
    if owner and owner.role == AI_BOT_ROLE:
        owner = await db.get(User, DEFAULT_USER_ID)
    return owner.default_profile_id if owner else None


async def resolve_schedule_profile(sched, db) -> str:
    """Resolve which AI profile a schedule's fired task should use.

    Schedules used to snapshot the owner's ``default_profile_id`` at
    creation time and treat that snapshot as authoritative forever.
    That broke provider failover: switching the default (e.g. to
    deepseek during a Claude outage) silently left every existing
    schedule pinned to the old provider, so scheduled auto-fires kept
    hitting Anthropic while manual tasks (which resolve the default
    live) recovered.

    The fix: an unpinned schedule follows the owner's *current*
    ``default_profile_id``, resolved live at every fire. Both ``None``
    and the literal ``'default'`` mean "inherit" — ``'default'`` is the
    ``Schedule.ai_profile_id`` column default (so a freshly-created
    unpinned schedule holds ``'default'``, never NULL) and was the
    legacy creation-time snapshot, so treating it as inherit needs no
    model/column change or data backfill. Any other non-empty value is
    honored as an explicit per-schedule pin.

    ``sched`` may be ``None`` (e.g. an ad-hoc cron job with no
    schedule row); in that case there is no owner to resolve, so this
    returns ``DEFAULT_PROFILE_ID`` (the global inherit sentinel) and the
    caller applies its own default.

    **System schedules have no human owner** — they are seeded with
    ``created_by=AI_BOT_USER_ID``, and the nightly catalog sync died
    with "Not logged in · Please run /login" every night for weeks
    because of it. That fall-through is
    :func:`resolve_owner_profile`'s, shared with task creation so the
    two paths cannot drift apart again.
    """
    if (
        sched
        and sched.ai_profile_id
        and sched.ai_profile_id != DEFAULT_PROFILE_ID
    ):
        return sched.ai_profile_id
    if sched and sched.created_by:
        owner = await db.get(User, sched.created_by)
        resolved = await resolve_owner_profile(owner, db)
        if resolved:
            return resolved
    return DEFAULT_PROFILE_ID
