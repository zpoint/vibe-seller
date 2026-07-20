"""AI Profile management for chat-level agent switching."""

import json
import os

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

# Hardcoded provider presets — everything except the API key
PROVIDER_PRESETS = {
    # Kimi K3 (launched 2026-07-16, 1M context). The ``[1m]`` suffix
    # selects the 1M-context variant and is a Claude-Code-env-var-only
    # convention (bare ``k3`` is the 256K form); Moonshot's docs state
    # this explicitly. Kimi does not distinguish a small/fast tier, so
    # every model slot uses the same id.
    # https://www.kimi.com/code/docs/en/third-party-tools/other-coding-agents.html
    'kimi': {
        'name': 'Kimi',
        'description': 'Kimi K3 (1M context) via Moonshot',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.kimi.com/coding/',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ENABLE_TOOL_SEARCH': 'False',
            'ANTHROPIC_MODEL': 'k3[1m]',
            'ANTHROPIC_SMALL_FAST_MODEL': 'k3[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'k3[1m]',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'k3[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'k3[1m]',
            'CLAUDE_CODE_SUBAGENT_MODEL': 'k3[1m]',
        },
    },
    # MiniMax-M3 with the ``[1m]`` suffix per MiniMax's Claude Code doc
    # (all tier slots use the 1M-context id). No distinct small/fast.
    # https://platform.minimax.io/docs/token-plan/claude-code
    'minimax': {
        'name': 'MiniMax',
        'description': 'MiniMax-M3 (1M context)',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.minimaxi.com/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'MiniMax-M3[1m]',
            'ANTHROPIC_SMALL_FAST_MODEL': 'MiniMax-M3[1m]',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'MiniMax-M3[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'MiniMax-M3[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'MiniMax-M3[1m]',
        },
    },
    # GLM-5.2: the ``[1m]`` suffix selects the 1M-context variant (same
    # convention as DeepSeek's ``[1m]``). CLAUDE_CODE_AUTO_COMPACT_WINDOW
    # must be raised to 1M, else Claude Code auto-compacts long before the
    # model's 1M window is reached. https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2
    'glm': {
        'name': 'GLM (China)',
        'group': 'GLM',
        'variant': 'China',
        'description': 'GLM-5.2 (1M context) via ZhiPu BigModel',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://open.bigmodel.cn/api/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'glm-5.2[1m]',
            'ANTHROPIC_SMALL_FAST_MODEL': 'glm-4.7',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'glm-5.2[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'glm-5.2[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'glm-4.7',
            'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '1000000',
        },
    },
    'glm_intl': {
        'name': 'GLM (International)',
        'group': 'GLM',
        'variant': 'International',
        'description': 'GLM-5.2 (1M context) via Z.AI',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.z.ai/api/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'glm-5.2[1m]',
            'ANTHROPIC_SMALL_FAST_MODEL': 'glm-4.7',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'glm-5.2[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'glm-5.2[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'glm-4.7',
            'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '1000000',
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
        'name': 'Qwen (Pay-as-you-go, China)',
        'group': 'Alibaba Cloud',
        'variant': 'Pay-as-you-go (China)',
        'description': (
            'Qwen3.7-Max via Alibaba Cloud Bailian, pay-as-you-go billing '
            '(Beijing region)'
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
            'CLAUDE_CODE_SUBAGENT_MODEL': 'qwen3.7-max',
            # Qwen 1M via the doc's token-count method; the [1m]
            # suffix 400s on the DashScope endpoint (live-verified).
            'CLAUDE_CODE_MAX_CONTEXT_TOKENS': '1000000',
        },
    },
    # International (Singapore) pay-as-you-go uses a per-workspace MaaS
    # host — the user must replace {WorkspaceId} in the Base URL with
    # their real Workspace ID (the placeholder deliberately fails the
    # save-time probe until they do). Same models as the China tier.
    'qwen_intl': {
        'name': 'Qwen (Pay-as-you-go, International)',
        'group': 'Alibaba Cloud',
        'variant': 'Pay-as-you-go (International)',
        'description': (
            'Qwen3.7-Max via Alibaba Cloud Model Studio, pay-as-you-go '
            '(Singapore). Replace {WorkspaceId} in the Base URL.'
        ),
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': (
                'https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com'
                '/apps/anthropic'
            ),
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'qwen3.7-max',
            'ANTHROPIC_SMALL_FAST_MODEL': 'qwen3.6-flash',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'qwen3.7-max',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'qwen3.7-max',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'qwen3.6-flash',
            'CLAUDE_CODE_SUBAGENT_MODEL': 'qwen3.7-max',
            # Qwen 1M via the doc's token-count method; the [1m]
            # suffix 400s on the DashScope endpoint (live-verified).
            'CLAUDE_CODE_MAX_CONTEXT_TOKENS': '1000000',
        },
    },
    'qwen_coding': {
        'name': 'Qwen (Coding Plan)',
        'group': 'Alibaba Cloud',
        'variant': 'Coding Plan',
        'description': (
            'Qwen3.7-Plus via Alibaba Cloud Bailian Coding Plan '
            '(monthly subscription)'
        ),
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': (
                'https://coding.dashscope.aliyuncs.com/apps/anthropic'
            ),
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'qwen3.7-plus',
            'ANTHROPIC_SMALL_FAST_MODEL': 'qwen3.7-plus',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'qwen3.7-plus',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'qwen3.7-plus',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'qwen3.7-plus',
            'CLAUDE_CODE_SUBAGENT_MODEL': 'qwen3.7-plus',
            'CLAUDE_CODE_MAX_CONTEXT_TOKENS': '1000000',
        },
    },
    # Token Plan (个人版 and 团队版 share this config; they differ only by
    # which sk-sp- key you use). Newer Bailian plan with the
    # qwen3.8-max-preview flagship on a distinct MaaS base URL; the
    # ~960K context window is set explicitly per Alibaba's doc.
    # qwen3.8-max-preview is Token-Plan-only (401s on pay-as-you-go).
    # https://help.aliyun.com/zh/model-studio/claude-code
    'qwen_token': {
        'name': 'Qwen (Token Plan)',
        'group': 'Alibaba Cloud',
        'variant': 'Token Plan',
        'description': (
            'Qwen3.8-Max-Preview via Alibaba Cloud Bailian Token Plan '
            '(个人版 / 团队版)'
        ),
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': (
                'https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic'
            ),
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'qwen3.8-max-preview',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'qwen3.6-flash',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'qwen3.8-max-preview',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'qwen3.8-max-preview',
            'CLAUDE_CODE_SUBAGENT_MODEL': 'qwen3.7-max',
            'CLAUDE_CODE_MAX_CONTEXT_TOKENS': '983616',
        },
    },
}


# Selectable model ids per provider, surfaced as a dropdown in the
# config UI so a user can trade the flagship for a cheaper/older model
# (e.g. MiniMax M2.5 instead of M3) without hand-editing env vars.
#
# Each entry: ``id`` (exact API string), ``label`` (UI text), and
# best-effort ``context`` / ``vision`` metadata shown as badges. The
# FIRST entry is the default and MUST equal the preset's
# ``ANTHROPIC_MODEL`` (pinned by a test). The UI always also offers a
# free-text "Custom" entry, and every choice is endpoint-validated on
# save, so this is a convenience shortlist — not an exhaustive or
# load-bearing catalog. Vendors rev these often; stale entries are
# caught at save time by the validation probe rather than silently
# breaking a task run.
#
# ``vision`` matters because the agent feeds browser screenshots to the
# model — a text-only model degrades badly on browser automation. It is
# doc-sourced (conservative): DeepSeek V4's public API documents no
# image input, so it is labeled text-only even though the live endpoint
# does not reject image payloads. Omit ``vision`` when genuinely
# unknown rather than guessing. ``context`` is the model's native
# window (these providers don't gate 1M behind the ``[1m]`` suffix the
# way Anthropic does — the suffix only tells Claude Code to advertise
# the window).
PROVIDER_MODELS = {
    # Model dropdown options per provider (id + label + optional context
    # / vision badges). First entry is the default and MUST equal the
    # preset's ANTHROPIC_MODEL (pinned by a test). The UI also offers a
    # free-text field, and every choice is endpoint-validated on save.
    #
    # VISION LABELS ARE LIVE-VERIFIED where a key exists: each id below
    # was probed with a 64x64 solid-red AND solid-blue image, 3x, and is
    # only marked vision=True if it named BOTH correctly every time.
    # DeepSeek/MiniMax/Qwen were verified with the account keys. Kimi and
    # GLM have NO key available, so their vision is intentionally OMITTED
    # (no badge) rather than guessed. context is doc-sourced (not
    # practical to probe a 1M window).
    #
    # Kimi coding endpoint only accepts the short-form ids below; older
    # kimi-k2.5 etc. live on api.moonshot.ai and 400 here.
    # Vision: K3 (and the K2.7-code aliases) are natively multimodal per
    # Moonshot's official vision guide (platform.kimi.ai/docs/guide/
    # use-kimi-vision-model), cross-checked via context7. Doc-verified,
    # NOT live-probed (no key); the coding endpoint carries images per
    # Kimi Code's own changelog. Give me a Kimi key to confirm live.
    'kimi': [
        {
            'id': 'k3[1m]',
            'label': 'K3 (1M context)',
            'context': '1M',
            'vision': True,
        },
        {'id': 'k3', 'label': 'K3 (256K)', 'context': '256K', 'vision': True},
        {
            'id': 'kimi-for-coding',
            'label': 'Kimi for Coding',
            'vision': True,
        },
        {
            'id': 'kimi-for-coding-highspeed',
            'label': 'Kimi for Coding — high-speed',
            'vision': True,
        },
    ],
    # MiniMax: text-only. Live probe (red+blue x3) had M3 name red then
    # reply "i cannot see images" / wrong color, and M2.x return empty —
    # it accepts image blocks but does not reliably read them.
    'minimax': [
        {
            'id': 'MiniMax-M3[1m]',
            'label': 'M3 (1M context)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'MiniMax-M2.7',
            'label': 'M2.7',
            'context': '200K',
            'vision': False,
        },
        {
            'id': 'MiniMax-M2.5',
            'label': 'M2.5',
            'context': '200K',
            'vision': False,
        },
        {
            'id': 'MiniMax-M2.1',
            'label': 'M2.1',
            'context': '200K',
            'vision': False,
        },
    ],
    # GLM is TEXT-ONLY — live-verified on Z.AI (glm-5.2 / glm-4.7 both
    # misnamed a red/blue image); GLM's vision is a SEPARATE model line
    # (glm-4.5v / glm-4v). Ids below are live-verified alive on Z.AI. The
    # flagship default carries the ``[1m]`` 1M-context tag, which Claude
    # Code strips before calling the API (the save-time probe strips it
    # too — the raw endpoint 400s on the literal suffix).
    'glm': [
        {
            'id': 'glm-5.2[1m]',
            'label': 'GLM-5.2 (1M context)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'glm-4.7',
            'label': 'GLM-4.7',
            'context': '200K',
            'vision': False,
        },
        {
            'id': 'glm-5.1',
            'label': 'GLM-5.1',
            'context': '200K',
            'vision': False,
        },
        {
            'id': 'glm-4.6',
            'label': 'GLM-4.6',
            'context': '200K',
            'vision': False,
        },
    ],
    'glm_intl': [
        {
            'id': 'glm-5.2[1m]',
            'label': 'GLM-5.2 (1M context)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'glm-4.7',
            'label': 'GLM-4.7',
            'context': '200K',
            'vision': False,
        },
        {
            'id': 'glm-5.1',
            'label': 'GLM-5.1',
            'context': '200K',
            'vision': False,
        },
        {
            'id': 'glm-4.6',
            'label': 'GLM-4.6',
            'context': '200K',
            'vision': False,
        },
    ],
    # DeepSeek: text-only (live probe returned empty text on images).
    'deepseek': [
        {
            'id': 'deepseek-v4-pro[1m]',
            'label': 'V4 Pro (1M context)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'deepseek-v4-flash',
            'label': 'V4 Flash (fast)',
            'context': '1M',
            'vision': False,
        },
    ],
    # Qwen (live-verified on the pay-go endpoint): qwen3.7-max rejects
    # images (400) -> text-only; qwen3.7-plus / qwen3.6-flash / the VL
    # series read red+blue correctly -> vision.
    'qwen': [
        {
            'id': 'qwen3.7-max',
            'label': 'Qwen3.7-Max (flagship)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'qwen3.7-plus',
            'label': 'Qwen3.7-Plus (vision)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.6-flash',
            'label': 'Qwen3.6-Flash (fast, vision)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3-vl-plus',
            'label': 'Qwen3-VL-Plus (vision)',
            'vision': True,
        },
    ],
    'qwen_intl': [
        {
            'id': 'qwen3.7-max',
            'label': 'Qwen3.7-Max (flagship)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'qwen3.7-plus',
            'label': 'Qwen3.7-Plus (vision)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.6-flash',
            'label': 'Qwen3.6-Flash (fast, vision)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3-vl-plus',
            'label': 'Qwen3-VL-Plus (vision)',
            'vision': True,
        },
    ],
    # Coding Plan serves qwen3.7-plus (same id verified as vision on
    # pay-go).
    'qwen_coding': [
        {
            'id': 'qwen3.7-plus',
            'label': 'Qwen3.7-Plus (vision)',
            'context': '1M',
            'vision': True,
        },
    ],
    # Token Plan flagship qwen3.8-max-preview is Token-Plan-only (401s on
    # pay-go, no key to probe) -> vision omitted. The 3.7 ids reuse the
    # pay-go-verified labels.
    'qwen_token': [
        {
            'id': 'qwen3.8-max-preview',
            'label': 'Qwen3.8-Max-Preview (flagship)',
            'context': '1M',
        },
        {
            'id': 'qwen3.7-max',
            'label': 'Qwen3.7-Max',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'qwen3.7-plus',
            'label': 'Qwen3.7-Plus (vision)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.6-flash',
            'label': 'Qwen3.6-Flash (fast, vision)',
            'context': '1M',
            'vision': True,
        },
    ],
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
    """
    if (
        sched
        and sched.ai_profile_id
        and sched.ai_profile_id != DEFAULT_PROFILE_ID
    ):
        return sched.ai_profile_id
    if sched and sched.created_by:
        owner = await db.get(User, sched.created_by)
        if owner and owner.default_profile_id:
            return owner.default_profile_id
    return DEFAULT_PROFILE_ID
