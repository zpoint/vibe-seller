"""AI Profile management for chat-level agent switching."""

import json
import os

from app.ai.provider_presets import PROVIDER_MODELS, PROVIDER_PRESETS
from app.config import AI_BOT_ROLE, DEFAULT_USER_ID
from app.models.user import User
from app.workspace.manager import VIBE_SELLER_DIR

PROFILES_PATH = VIBE_SELLER_DIR / 'profiles.json'

DEFAULT_PROFILE_ID = 'default'

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

        Returns merged system env + profile overrides.
        """
        profile = ProfileManager.get_profile(profile_id)
        if not profile:
            return os.environ.copy()

        env = os.environ.copy()
        env.update(profile.get('env', {}))
        return env

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
