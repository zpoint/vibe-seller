"""AI Profile management for chat-level agent switching."""

import json
import os

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

# Hardcoded provider presets — everything except the API key
PROVIDER_PRESETS = {
    'kimi': {
        'name': 'Kimi',
        'description': 'Kimi K2.5 via Moonshot',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.kimi.com/coding/',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ENABLE_TOOL_SEARCH': 'False',
            'ANTHROPIC_MODEL': 'kimi-k2.5',
            'ANTHROPIC_SMALL_FAST_MODEL': 'kimi-k2.5',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'kimi-k2.5',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'kimi-k2.5',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'kimi-k2.5',
            'CLAUDE_CODE_SUBAGENT_MODEL': 'kimi-k2.5',
        },
    },
    'minimax': {
        'name': 'MiniMax',
        'description': 'MiniMax-M2.7',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.minimaxi.com/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'MiniMax-M2.7',
            'ANTHROPIC_SMALL_FAST_MODEL': 'MiniMax-M2.7',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'MiniMax-M2.7',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'MiniMax-M2.7',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'MiniMax-M2.7',
        },
    },
    'glm': {
        'name': 'GLM (China)',
        'description': 'GLM-5.1 via ZhiPu BigModel',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://open.bigmodel.cn/api/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'glm-5.1',
            'ANTHROPIC_SMALL_FAST_MODEL': 'glm-4.5-air',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'glm-5.1',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'glm-5.1',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'glm-4.5-air',
        },
    },
    'glm_intl': {
        'name': 'GLM (International)',
        'description': 'GLM-5.1 via Z.AI',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.z.ai/api/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'glm-5.1',
            'ANTHROPIC_SMALL_FAST_MODEL': 'glm-4.5-air',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'glm-5.1',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'glm-5.1',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'glm-4.5-air',
        },
    },
    # Env keys per DeepSeek's official integration doc:
    # https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/claude_code
    # The ``[1m]`` suffix on the pro model picks the 1M-context variant
    # (docs render the suffix literally in both EN and CN examples).
    'deepseek': {
        'name': 'DeepSeek',
        'description': 'DeepSeek V4 Pro (1M context) via DeepSeek API',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'deepseek-v4-pro[1m]',
            'ANTHROPIC_SMALL_FAST_MODEL': 'deepseek-v4-flash',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'deepseek-v4-pro[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'deepseek-v4-pro[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'deepseek-v4-flash',
            'CLAUDE_CODE_SUBAGENT_MODEL': 'deepseek-v4-flash',
            'CLAUDE_CODE_EFFORT_LEVEL': 'max',
        },
    },
    # Alibaba Cloud / DashScope has two separate Anthropic endpoints —
    # pay-as-you-go and Coding Plan — with DIFFERENT base URLs and
    # different recommended models. Keep them as two presets so the
    # UI can offer the right one based on the user's subscription.
    # https://help.aliyun.com/zh/model-studio/claude-code
    # https://help.aliyun.com/zh/model-studio/claude-code-coding-plan
    'qwen': {
        'name': 'Qwen (Pay-as-you-go)',
        'description': (
            'Qwen3.7-Max via Alibaba Cloud DashScope, pay-as-you-go billing'
        ),
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': (
                'https://dashscope.aliyuncs.com/apps/anthropic'
            ),
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'qwen3.7-max',
            'ANTHROPIC_SMALL_FAST_MODEL': 'qwen3.6-flash',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'qwen3.7-max',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'qwen3.7-max',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'qwen3.6-flash',
        },
    },
    'qwen_coding': {
        'name': 'Qwen (Coding Plan)',
        'description': (
            'Qwen3.6-Plus via Alibaba Cloud DashScope Coding Plan '
            '(monthly subscription)'
        ),
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': (
                'https://coding.dashscope.aliyuncs.com/apps/anthropic'
            ),
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'qwen3.6-plus',
            'ANTHROPIC_SMALL_FAST_MODEL': 'qwen3.6-plus',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'qwen3.6-plus',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'qwen3.6-plus',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'qwen3.6-plus',
            'CLAUDE_CODE_SUBAGENT_MODEL': 'qwen3.6-plus',
        },
    },
}


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
        """Return hardcoded provider presets for the UI."""
        return PROVIDER_PRESETS


def profile_kind(profile: dict | None) -> str:
    """Return a privacy-safe enum identifying which provider a profile uses.

    Matches the profile's ANTHROPIC_BASE_URL against PROVIDER_PRESETS
    to get one of: kimi/minimax/glm/glm_intl/deepseek/qwen/qwen_coding/
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
