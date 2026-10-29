"""Built-in AI provider presets and their selectable model ids.

Data only: everything a provider preset sets except the API key, and
the per-provider model dropdown. ``app.ai.profiles`` re-exports both.
"""

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
