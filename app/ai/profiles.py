"""AI Profile management for chat-level agent switching."""

import json
import os

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

# Hardcoded provider presets — everything except the API key. Aligned
# with each provider's Claude Code integration doc (each preset cites
# its URL). No provider doc still sets the legacy
# ``ANTHROPIC_SMALL_FAST_MODEL`` (Kimi's own cleanup script deletes it
# as a leftover), so presets use the ``ANTHROPIC_DEFAULT_*_MODEL`` tier
# names only — including the newer FABLE tier slot where documented.
PROVIDER_PRESETS = {
    # Kimi K3.1 Pro (rolled out 2026-10; 1M context, thinking-only —
    # requests with thinking disabled are rejected). The ``[1m]``
    # suffix selects the 1M-context variant and is a Claude-Code-env-
    # var-only convention (bare ``k3.1-pro`` caps at 256K on the Plus
    # tier, 1M on Pro+); Moonshot's docs state this explicitly. Kimi
    # does not distinguish a small/fast tier, so every model slot uses
    # the same id. Note the endpoint domain moved from api.kimi.com to
    # api.kimi.ai.
    # https://www.kimi.com/code/docs/en/third-party-tools/claude-code.html
    'kimi': {
        'name': 'Kimi',
        'description': 'Kimi K3.1 Pro (1M context) via Moonshot',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.kimi.ai/coding/',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ENABLE_TOOL_SEARCH': 'False',
            'ANTHROPIC_MODEL': 'k3.1-pro[1m]',
            'ANTHROPIC_DEFAULT_FABLE_MODEL': 'k3.1-pro[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'k3.1-pro[1m]',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'k3.1-pro[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'k3.1-pro[1m]',
            'CLAUDE_CODE_SUBAGENT_MODEL': 'k3.1-pro[1m]',
            'CLAUDE_CODE_EFFORT_LEVEL': 'high',
            'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '1048576',
            'CLAUDE_CODE_MAX_CONTEXT_TOKENS': '1048576',
        },
    },
    # MiniMax M3.1-Flash per MiniMax's Claude Code doc (all tier slots
    # use the 1M-context id; thinking is always on and tuned via
    # ``effort``). MiniMax recommends a 512K auto-compact window and
    # ``effort=max``. Keys are region-locked: China subscription keys
    # must use api.minimax.cn (live-verified 2026-10-29), international
    # keys api.minimax.io — the legacy api.minimaxi.com still serves
    # both regions but is no longer what the docs name.
    # https://platform.minimaxi.com/docs/token-plan/claude-code
    # https://platform.minimax.io/docs/token-plan/claude-code
    'minimax': {
        'name': 'MiniMax',
        'description': 'MiniMax M3.1 Flash (1M context)',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.minimax.cn/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'MiniMax-M3.1-Flash[1m]',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'MiniMax-M3.1-Flash[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'MiniMax-M3.1-Flash[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'MiniMax-M3.1-Flash[1m]',
            'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '524288',
            'CLAUDE_CODE_EFFORT_LEVEL': 'max',
        },
    },
    # GLM-5.3: the ``[1m]`` suffix selects the 1M-context variant (same
    # convention as DeepSeek's ``[1m]``). CLAUDE_CODE_AUTO_COMPACT_WINDOW
    # must be raised to 1M, else Claude Code auto-compacts long before
    # the model's 1M window is reached. Zhipu's own Claude Code config
    # maps the haiku tier to glm-5.3-flash (the natively multimodal
    # budget tier, ~1/10 the flagship price; the flagship itself is
    # text-only).
    # https://docs.bigmodel.cn/cn/coding-plan/latest-model.md
    # https://docs.bigmodel.cn/cn/guide/develop/claude/introduction.md
    'glm': {
        'name': 'GLM (China)',
        'group': 'GLM',
        'variant': 'China',
        'description': 'GLM-5.3 (1M context) via ZhiPu BigModel',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://open.bigmodel.cn/api/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'glm-5.3[1m]',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'glm-5.3[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'glm-5.3[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'glm-5.3-flash[1m]',
            'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '1000000',
        },
    },
    # Same models on Z.AI's international endpoint; the env mirrors
    # Z.AI's own Claude Code doc.
    # https://docs.z.ai/devpack/tool/claude.md
    'glm_intl': {
        'name': 'GLM (International)',
        'group': 'GLM',
        'variant': 'International',
        'description': 'GLM-5.3 (1M context) via Z.AI',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.z.ai/api/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'glm-5.3[1m]',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'glm-5.3[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'glm-5.3[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'glm-5.3-flash[1m]',
            'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '1000000',
        },
    },
    # Env keys per DeepSeek's official integration doc:
    # https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/claude_code
    # The doc now recommends deepseek-flash (DeepSeek-V4.1-Flash) for
    # the main/sonnet/opus slots — unlike deepseek-v4-pro it reads
    # images — with the ``[1m]`` suffix picking the 1M-context variant.
    # The old deepseek-v4-flash id is retired upstream (still accepted
    # as an alias of V4.1-Flash-1017), and claude-opus-* model names
    # map server-side to deepseek-v4-pro, so the flagship remains one
    # dropdown pick away.
    'deepseek': {
        'name': 'DeepSeek',
        'description': 'DeepSeek Flash (1M context) via DeepSeek API',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'deepseek-flash[1m]',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'deepseek-flash[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'deepseek-flash[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'deepseek-flash',
            'CLAUDE_CODE_SUBAGENT_MODEL': 'deepseek-flash',
            'CLAUDE_CODE_EFFORT_LEVEL': 'max',
            'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '786432',
        },
    },
    # Alibaba Cloud / Bailian has separate Anthropic endpoints —
    # pay-as-you-go (CN + international regions), Coding Plan, and
    # Token Plan — with DIFFERENT base URLs and different recommended
    # models. Keep them as separate presets so the UI can offer the
    # right one based on the user's subscription; the pay-go models
    # below follow Alibaba's own config block.
    # https://help.aliyun.com/zh/model-studio/claude-code
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
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'qwen3.7-plus',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'qwen3.7-plus',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'qwen3.7-plus',
            'CLAUDE_CODE_SUBAGENT_MODEL': 'qwen3.7-plus',
            'CLAUDE_CODE_MAX_CONTEXT_TOKENS': '1000000',
        },
    },
    # Token Plan (个人版 and 团队版 share this config; they differ only by
    # which sk- key you use) on a distinct MaaS base URL. Alibaba's
    # current recommendation routes the main + subagent slots through
    # ``auto`` with explicit tier defaults (haiku qwen3.6-flash, sonnet
    # qwen3.8-flash, opus qwen3.8-max — the preview id graduated to
    # GA). The ~960K context budget is set explicitly per the doc.
    # https://help.aliyun.com/zh/model-studio/claude-code
    'qwen_token': {
        'name': 'Qwen (Token Plan)',
        'group': 'Alibaba Cloud',
        'variant': 'Token Plan',
        'description': (
            'Qwen3.8-Max via Alibaba Cloud Bailian Token Plan (个人版 / 团队版)'
        ),
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': (
                'https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic'
            ),
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'auto',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'qwen3.6-flash',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'qwen3.8-flash',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'qwen3.8-max',
            'CLAUDE_CODE_SUBAGENT_MODEL': 'auto',
            'CLAUDE_CODE_MAX_CONTEXT_TOKENS': '983616',
        },
    },
}


# Selectable model ids per provider, surfaced as a dropdown in the
# config UI so a user can trade the flagship for a cheaper/older model
# (e.g. GLM-5.2 instead of GLM-5.3) without hand-editing env vars.
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
# live-probed where a key exists and doc-sourced otherwise; omit
# ``vision`` when genuinely unknown rather than guessing. ``context`` is
# the model's native window (these providers don't gate 1M behind the
# ``[1m]`` suffix the way Anthropic does — the suffix only tells Claude
# Code to advertise the window).
PROVIDER_MODELS = {
    # Model dropdown options per provider (id + label + optional context
    # / vision badges). First entry is the default and MUST equal the
    # preset's ANTHROPIC_MODEL (pinned by a test). The UI also offers a
    # free-text field, and every choice is endpoint-validated on save.
    #
    # VISION LABELS ARE LIVE-VERIFIED where a key exists: each id below
    # was probed with a 64x64 solid-red AND solid-blue image, 3x, and is
    # only marked vision=True if it named BOTH correctly every time
    # (2026-10-29 probes: GLM via Z.AI, MiniMax via api.minimax.cn,
    # DeepSeek direct). Kimi has no usable key (the account's Kimi Code
    # subscription lapsed — every id 403s before the model is reached),
    # so its vision is doc-verified only. context is doc-sourced (not
    # practical to probe a 1M window).
    #
    # Kimi coding endpoint only accepts the short-form ids below; older
    # kimi-k2.5 etc. live on api.moonshot.ai and 400 here. K3.1 Pro is
    # thinking-only (a request with thinking disabled is rejected).
    # Vision: the K-series coding ids are natively multimodal per
    # Moonshot's official vision guide (platform.kimi.ai/docs/guide/
    # use-kimi-vision-model), which lists kimi-k3.1-pro / kimi-k3 /
    # kimi-k2.7-code(-highspeed) explicitly. Doc-verified, NOT
    # live-probed (no key); give me a Kimi key to confirm live.
    'kimi': [
        {
            'id': 'k3.1-pro[1m]',
            'label': 'K3.1 Pro (1M context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'k3.1-pro',
            'label': 'K3.1 Pro (256K)',
            'context': '256K',
            'vision': True,
        },
        {
            'id': 'k3.1-pro-highspeed',
            'label': 'K3.1 Pro Highspeed (1M)',
            'context': '1M',
            'vision': True,
        },
        {'id': 'k3[1m]', 'label': 'K3 (1M)', 'context': '1M', 'vision': True},
        {
            'id': 'k3-256k',
            'label': 'K3 (256K)',
            'context': '256K',
            'vision': True,
        },
        {
            'id': 'kimi-for-coding',
            'label': 'K2.8 Preview',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'kimi-for-coding-highspeed',
            'label': 'K2.8 Preview — high-speed',
            'context': '256K',
            'vision': True,
        },
    ],
    # MiniMax: only M3.1-Flash and M3 remain — MiniMax retired the
    # M2.x line (M2.1 earlier; M2.5/M2.7 left the platform with a
    # 2026-11-18 retirement date, after which plan requests route to
    # M3.1-Flash). Both survivors are multimodal and LIVE-VERIFIED
    # (red+blue x3 each, api.minimax.cn, 2026-10-29); M3.1-Flash
    # always thinks and is tuned via effort.
    # https://platform.minimax.io/docs/faq/models.md
    'minimax': [
        {
            'id': 'MiniMax-M3.1-Flash[1m]',
            'label': 'M3.1 Flash (1M context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'MiniMax-M3.1-Flash',
            'label': 'M3.1 Flash',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'MiniMax-M3[1m]',
            'label': 'M3 (1M context)',
            'context': '1M',
            'vision': True,
        },
        {'id': 'MiniMax-M3', 'label': 'M3', 'context': '1M', 'vision': True},
    ],
    # GLM-5.3 is text-only (LIVE-verified 2026-10-29 on Z.AI: it
    # misnamed a red/blue image, matching Zhipu's own doc note that
    # "GLM-5.3 is a text-only model"); GLM-5.3-Flash is the natively
    # multimodal budget tier (LIVE-verified: red+blue x3 all correct).
    # The older glm-5.2 and glm-4.7 were live-verified text-only
    # earlier; 5.1/4.6 are the same text line. Ids below are
    # live-verified alive on Z.AI. The flagship default carries the
    # ``[1m]`` 1M-context tag, which Claude Code strips before calling
    # the API (the save-time probe strips it too — the raw endpoint
    # 400s on the literal suffix).
    'glm': [
        {
            'id': 'glm-5.3[1m]',
            'label': 'GLM-5.3 (1M context)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'glm-5.3-flash[1m]',
            'label': 'GLM-5.3-Flash (1M, vision)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'glm-5.2[1m]',
            'label': 'GLM-5.2 (1M context)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'glm-5.1',
            'label': 'GLM-5.1',
            'context': '200K',
            'vision': False,
        },
        {
            'id': 'glm-4.7',
            'label': 'GLM-4.7',
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
            'id': 'glm-5.3[1m]',
            'label': 'GLM-5.3 (1M context)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'glm-5.3-flash[1m]',
            'label': 'GLM-5.3-Flash (1M, vision)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'glm-5.2[1m]',
            'label': 'GLM-5.2 (1M context)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'glm-5.1',
            'label': 'GLM-5.1',
            'context': '200K',
            'vision': False,
        },
        {
            'id': 'glm-4.7',
            'label': 'GLM-4.7',
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
    # DeepSeek: deepseek-flash (DeepSeek-V4.1-Flash) reads images —
    # LIVE-verified (red+blue x3) and documented on the pricing page
    # (图像理解: 支持) — while deepseek-v4-pro is text-only (doc +
    # earlier live probe returned empty on images). deepseek-v4-flash
    # is retired (an alias of V4.1-Flash-1017) and not listed.
    'deepseek': [
        {
            'id': 'deepseek-flash[1m]',
            'label': 'Flash V4.1 (1M, vision)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'deepseek-flash',
            'label': 'Flash V4.1 (fast, vision)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'deepseek-v4-pro[1m]',
            'label': 'V4 Pro (1M context)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'deepseek-v4-pro',
            'label': 'V4 Pro',
            'context': '1M',
            'vision': False,
        },
    ],
    # Qwen (live-verified earlier on the pay-go endpoint): qwen3.7-max
    # rejects images (400) -> text-only; qwen3.7-plus / qwen3.6-flash /
    # the VL series read red+blue correctly -> vision. qwen3.8-max /
    # qwen3.8-flash are new since that probe with no key to verify —
    # vision omitted rather than guessed.
    'qwen': [
        {
            'id': 'qwen3.7-max',
            'label': 'Qwen3.7-Max (flagship)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'qwen3.8-max',
            'label': 'Qwen3.8-Max (newer flagship)',
            'context': '1M',
        },
        {
            'id': 'qwen3.8-flash',
            'label': 'Qwen3.8-Flash (fast)',
            'context': '1M',
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
            'id': 'qwen3.8-max',
            'label': 'Qwen3.8-Max (newer flagship)',
            'context': '1M',
        },
        {
            'id': 'qwen3.8-flash',
            'label': 'Qwen3.8-Flash (fast)',
            'context': '1M',
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
    # Token Plan flagship routes through ``auto`` (Alibaba's own
    # default); qwen3.8-max graduated from preview to GA. The 3.7/3.6
    # ids reuse the pay-go-verified labels. qwen3.8 ids and auto have
    # no key to probe -> vision omitted.
    'qwen_token': [
        {
            'id': 'auto',
            'label': 'Auto (provider-routed)',
        },
        {
            'id': 'qwen3.8-max',
            'label': 'Qwen3.8-Max (flagship)',
            'context': '1M',
        },
        {
            'id': 'qwen3.8-flash',
            'label': 'Qwen3.8-Flash (fast)',
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
