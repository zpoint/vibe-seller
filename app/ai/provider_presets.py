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
    #
    # Host check (2026-11-09): the English Kimi Code doc names
    # https://api.kimi.ai/coding/, the Chinese one still names
    # https://api.kimi.com/coding/; the two regions' docs disagree and
    # neither names the other host. Both hosts answer an unauthenticated
    # /v1/messages with 401 (alive). It is one product, one console and
    # one key, so this stays a single variant on the host the preset has
    # always used — existing profiles and telemetry's base-URL match keep
    # working unchanged.
    # https://www.kimi.com/code/docs/en/third-party-tools/claude-code.html
    # https://www.kimi.com/code/docs/third-party-tools/claude-code.html
    'kimi': {
        'name': 'Kimi',
        'group': 'Kimi',
        'variant': 'Kimi Code (membership)',
        'description': 'Kimi K3.1 Pro (1M context) via Kimi Code membership',
        'purchase_url': ('https://www.kimi.com/membership/pricing'),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#kimi',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#kimi',
        },
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
    # Kimi API open platform (pay-as-you-go), a different product from
    # Kimi Code: keys from platform.kimi.com (China) / platform.kimi.ai
    # (international), each bound to its own host — api.moonshot.cn and
    # api.moonshot.ai respectively, as each region's Claude Code guide
    # names. Model ids carry the ``kimi-`` prefix (kimi-k3), unlike the
    # Kimi Code short ids above.
    #
    # The guides map HAIKU to kimi-k2.7-code, which is thinking-only
    # (400 "only type=enabled is allowed" when thinking is off). Claude
    # Code's haiku-tier calls (background summaries etc.) don't
    # guarantee thinking, so HAIKU uses kimi-k3[1m] like every other
    # slot — the guide's own default model, thinking optional.
    # https://platform.kimi.com/docs/guide/claude-code-kimi
    # https://platform.kimi.ai/docs/guide/claude-code-kimi
    'kimi_paygo': {
        'name': 'Kimi (Pay-as-you-go, China)',
        'group': 'Kimi',
        'variant': 'Pay-as-you-go (China)',
        'description': 'Kimi K3 (1M context) via the Kimi API open platform',
        'purchase_url': ('https://platform.kimi.com/'),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#kimi',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#kimi',
        },
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.moonshot.cn/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'kimi-k3[1m]',
            'ANTHROPIC_DEFAULT_FABLE_MODEL': 'kimi-k3[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'kimi-k3[1m]',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'kimi-k3[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'kimi-k3[1m]',
            'CLAUDE_CODE_SUBAGENT_MODEL': 'kimi-k3[1m]',
            'CLAUDE_CODE_EFFORT_LEVEL': 'max',
            'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '1000000',
        },
    },
    'kimi_paygo_intl': {
        'name': 'Kimi (Pay-as-you-go, International)',
        'group': 'Kimi',
        'variant': 'Pay-as-you-go (International)',
        'description': (
            'Kimi K3 (1M context) via the Kimi API platform (platform.kimi.ai)'
        ),
        'purchase_url': ('https://platform.kimi.ai/'),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#kimi',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#kimi',
        },
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.moonshot.ai/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'kimi-k3[1m]',
            'ANTHROPIC_DEFAULT_FABLE_MODEL': 'kimi-k3[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'kimi-k3[1m]',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'kimi-k3[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'kimi-k3[1m]',
            'CLAUDE_CODE_SUBAGENT_MODEL': 'kimi-k3[1m]',
            'CLAUDE_CODE_EFFORT_LEVEL': 'max',
            'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '1000000',
        },
    },
    # MiniMax M3.1-Flash per MiniMax's Claude Code doc (all tier slots
    # use the 1M-context id; thinking is always on and tuned via
    # ``effort``). MiniMax recommends a 512K auto-compact window and
    # ``effort=max``. Keys are region-locked: China subscription keys
    # must use api.minimax.cn (live-verified 2026-10-29), international
    # keys api.minimax.io — the legacy api.minimaxi.com still serves
    # both regions but is no longer what the docs name.
    # The same host serves both pay-as-you-go keys and M Plan
    # subscription keys (the key decides the billing), so payment path
    # needs no variant; region does.
    # https://platform.minimaxi.com/docs/token-plan/claude-code
    'minimax': {
        'name': 'MiniMax (China)',
        'group': 'MiniMax',
        'variant': 'China',
        'description': 'MiniMax M3.1 Flash (1M context), China (minimax.cn)',
        'purchase_url': ('https://platform.minimax.cn/'),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#minimax',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#minimax',
        },
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
    # International accounts (platform.minimax.io): identical config on
    # api.minimax.io, per the international Claude Code doc.
    # https://platform.minimax.io/docs/token-plan/claude-code
    'minimax_intl': {
        'name': 'MiniMax (International)',
        'group': 'MiniMax',
        'variant': 'International',
        'description': (
            'MiniMax M3.1 Flash (1M context), international (minimax.io)'
        ),
        'purchase_url': ('https://platform.minimax.io/'),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#minimax',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#minimax',
        },
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.minimax.io/anthropic',
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
        'purchase_url': ('https://open.bigmodel.cn/'),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#国内智谱开放平台',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#china-zhipu-bigmodel',
        },
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
        'purchase_url': ('https://z.ai/model-api'),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#海外zai',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#international-zai',
        },
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
        'purchase_url': ('https://platform.deepseek.com/top_up'),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#deepseek',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#deepseek',
        },
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
        'purchase_url': (
            'https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key'
        ),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#通义千问阿里云百炼',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#qwen-alibaba-cloud-model-studio',
        },
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
    # International (Singapore) pay-as-you-go. The doc's quick start
    # shows a per-workspace host ({WorkspaceId}.ap-southeast-1.maas...),
    # which forced users to hand-edit the Base URL; its access-method
    # table (401 troubleshooting) names the shared host below for a
    # Model Studio pay-as-you-go key — the international twin of the
    # China preset's dashscope.aliyuncs.com — so the preset uses that and
    # needs no editing. Same models as the China tier.
    # https://www.alibabacloud.com/help/en/model-studio/claude-code
    'qwen_intl': {
        'name': 'Qwen (Pay-as-you-go, International)',
        'group': 'Alibaba Cloud',
        'variant': 'Pay-as-you-go (International)',
        'description': (
            'Qwen3.7-Max via Alibaba Cloud Model Studio, pay-as-you-go '
            '(Singapore)'
        ),
        'purchase_url': ('https://modelstudio.console.alibabacloud.com/'),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#通义千问阿里云百炼',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#qwen-alibaba-cloud-model-studio',
        },
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': (
                'https://dashscope-intl.aliyuncs.com/apps/anthropic'
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
    # Token Plan (个人版 and 团队版 share this config; they differ only by
    # which sk- key you use) on a distinct MaaS base URL. Alibaba's
    # current recommendation routes the main + subagent slots through
    # ``auto`` with explicit tier defaults (haiku qwen3.6-flash, sonnet
    # qwen3.8-flash, opus qwen3.8-max — the preview id graduated to
    # GA). The ~960K context budget is set explicitly per the doc.
    # https://help.aliyun.com/zh/model-studio/claude-code
    'qwen_token': {
        'name': 'Qwen (Token Plan, China)',
        'group': 'Alibaba Cloud',
        'variant': 'Token Plan (China)',
        'description': (
            'Qwen3.8-Max via Alibaba Cloud Bailian Token Plan (个人版 / 团队版)'
        ),
        'purchase_url': (
            'https://bailian.console.aliyun.com/cn-beijing/subscription/token-plan/personal'
        ),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#通义千问阿里云百炼',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#qwen-alibaba-cloud-model-studio',
        },
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
    # International (Singapore) Token Plan: same model block as China on
    # the ap-southeast-1 Token Plan host, per the international doc.
    # https://www.alibabacloud.com/help/en/model-studio/claude-code
    'qwen_token_intl': {
        'name': 'Qwen (Token Plan, International)',
        'group': 'Alibaba Cloud',
        'variant': 'Token Plan (International)',
        'description': (
            'Qwen3.8-Max via Alibaba Cloud Model Studio Token Plan (Singapore)'
        ),
        'purchase_url': (
            'https://modelstudio.console.alibabacloud.com/subscription/token-plan/personal'
        ),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#通义千问阿里云百炼',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#qwen-alibaba-cloud-model-studio',
        },
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': (
                'https://token-plan.ap-southeast-1.maas.aliyuncs.com'
                '/apps/anthropic'
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
    # Coding Plan is closed to new subscribers (Alibaba stopped selling
    # it); only existing subscribers can still use it, so it sits last in
    # the Alibaba Cloud row. The label stays plain: subscribers recognise
    # their plan, and new users cannot buy it anyway.
    'qwen_coding': {
        'name': 'Qwen (Coding Plan, China)',
        'group': 'Alibaba Cloud',
        'variant': 'Coding Plan (China)',
        'description': (
            'Qwen3.7-Plus via Alibaba Cloud Bailian Coding Plan '
            '(monthly subscription)'
        ),
        'purchase_url': (
            'https://bailian.console.aliyun.com/cn-beijing/subscription/coding-plan'
        ),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#通义千问阿里云百炼',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#qwen-alibaba-cloud-model-studio',
        },
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
    # International (Singapore) Coding Plan: its own host and its own
    # Coding Plan key from the ap-southeast-1 console; same model block
    # as the China plan per the international doc.
    # https://www.alibabacloud.com/help/en/model-studio/claude-code
    'qwen_coding_intl': {
        'name': 'Qwen (Coding Plan, International)',
        'group': 'Alibaba Cloud',
        'variant': 'Coding Plan (International)',
        'description': (
            'Qwen3.7-Plus via Alibaba Cloud Model Studio Coding Plan '
            '(Singapore)'
        ),
        'purchase_url': (
            'https://modelstudio.console.alibabacloud.com/ap-southeast-1/subscription/coding-plan'
        ),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#通义千问阿里云百炼',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#qwen-alibaba-cloud-model-studio',
        },
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': (
                'https://coding-intl.dashscope.aliyuncs.com/apps/anthropic'
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
    # Xiaomi MiMo — Anthropic-Messages-compatible endpoint, documented
    # for Claude Code by name. Two products on two hosts: the metered
    # pay-as-you-go API (``sk-`` keys) and the Token Plan subscription
    # (``tp-``/``ttp-`` keys), whose hosts are region-locked (China /
    # Singapore / Europe clusters).
    #
    # Auth is ``Authorization: Bearer`` (MiMo documents ``api-key`` and
    # ``Bearer``, NOT ``x-api-key``), so the key must go in
    # ``ANTHROPIC_AUTH_TOKEN`` — the row the config modal seeds first.
    #
    # MiMo's own Claude Code block maps EVERY tier slot to the flagship
    # and sets no subagent model or window vars; the presets follow it.
    # The ``[1m]`` suffix is the doc's own way to get the 1M window
    # ("可以在模型 ID 后加上 [1m] 后缀以扩展上下文") and is all Claude Code
    # needs to size the window — no compact/max-context override.
    # https://mimo.mi.com/docs/zh-CN/tokenplan/integration/claudecode
    # https://mimo.mi.com/docs/api/chat/anthropic-api
    'mimo': {
        'name': 'MiMo (Pay-as-you-go)',
        'group': 'MiMo',
        'variant': 'Pay-as-you-go',
        'description': 'MiMo V2.6 Pro (1M context) via Xiaomi MiMo',
        'purchase_url': ('https://platform.xiaomimimo.com/#/console/balance'),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#小米-mimo',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#xiaomi-mimo',
        },
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': 'https://api.xiaomimimo.com/anthropic',
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'mimo-v2.6-pro[1m]',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'mimo-v2.6-pro[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'mimo-v2.6-pro[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'mimo-v2.6-pro[1m]',
        },
    },
    # Token Plan (subscription), one preset per documented cluster: China
    # ``token-plan-cn``, Singapore ``token-plan-sgp``, Europe
    # ``token-plan-ams`` — same paths and models, different host. The
    # Token Plan console page shows which cluster an account is on.
    # https://mimo.mi.com/docs/tokenplan/Token%20Plan/quick-access
    'mimo_token': {
        'name': 'MiMo (Token Plan)',
        'group': 'MiMo',
        'variant': 'Token Plan (China cluster)',
        'description': (
            'MiMo V2.6 Pro (1M context) via Xiaomi MiMo Token Plan '
            '(个人版 / 团队版)'
        ),
        'purchase_url': (
            'https://platform.xiaomimimo.com/#/console/plan-manage'
        ),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#小米-mimo',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#xiaomi-mimo',
        },
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': (
                'https://token-plan-cn.xiaomimimo.com/anthropic'
            ),
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'mimo-v2.6-pro[1m]',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'mimo-v2.6-pro[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'mimo-v2.6-pro[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'mimo-v2.6-pro[1m]',
        },
    },
    'mimo_token_sgp': {
        'name': 'MiMo (Token Plan, Singapore)',
        'group': 'MiMo',
        'variant': 'Token Plan (Singapore cluster)',
        'description': (
            'MiMo V2.6 Pro (1M context) via Xiaomi MiMo Token Plan, '
            'Singapore cluster'
        ),
        'purchase_url': (
            'https://platform.xiaomimimo.com/#/console/plan-manage'
        ),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#小米-mimo',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#xiaomi-mimo',
        },
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': (
                'https://token-plan-sgp.xiaomimimo.com/anthropic'
            ),
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'mimo-v2.6-pro[1m]',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'mimo-v2.6-pro[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'mimo-v2.6-pro[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'mimo-v2.6-pro[1m]',
        },
    },
    'mimo_token_ams': {
        'name': 'MiMo (Token Plan, Europe)',
        'group': 'MiMo',
        'variant': 'Token Plan (Europe cluster)',
        'description': (
            'MiMo V2.6 Pro (1M context) via Xiaomi MiMo Token Plan, '
            'Europe cluster'
        ),
        'purchase_url': (
            'https://platform.xiaomimimo.com/#/console/plan-manage'
        ),
        'guide_url': {
            'zh': 'https://docs.vibe-sellers.com/llm-keys/#小米-mimo',
            'en': 'https://docs.vibe-sellers.com/en/llm-keys/#xiaomi-mimo',
        },
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': (
                'https://token-plan-ams.xiaomimimo.com/anthropic'
            ),
            'API_TIMEOUT_MS': '3000000',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'ANTHROPIC_MODEL': 'mimo-v2.6-pro[1m]',
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'mimo-v2.6-pro[1m]',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'mimo-v2.6-pro[1m]',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'mimo-v2.6-pro[1m]',
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
            'id': 'k3.1-pro-highspeed',
            'label': 'K3.1 Pro Highspeed (1M context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'k3[1m]',
            'label': 'K3 (1M context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'kimi-for-coding',
            'label': 'K2.8 Preview',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'k3.1-pro',
            'label': 'K3.1 Pro (256K context)',
            'context': '256K',
            'vision': True,
        },
        {
            'id': 'k3-256k',
            'label': 'K3 (256K context)',
            'context': '256K',
            'vision': True,
        },
        {
            'id': 'kimi-for-coding-highspeed',
            'label': 'K2.8 Preview (high-speed)',
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
            'id': 'MiniMax-M3[1m]',
            'label': 'M3 (1M context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'MiniMax-M3.1-Flash',
            'label': 'M3.1 Flash (200K context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'MiniMax-M3',
            'label': 'M3 (200K context)',
            'context': '1M',
            'vision': True,
        },
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
            'label': 'GLM-5.3-Flash (1M context)',
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
            'label': 'GLM-5.3-Flash (1M context)',
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
            'label': 'Flash V4.1 (1M context)',
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
            'id': 'deepseek-flash',
            'label': 'Flash V4.1 (200K context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'deepseek-v4-pro',
            'label': 'V4 Pro (200K context)',
            'context': '1M',
            'vision': False,
        },
    ],
    # Qwen (live-verified earlier on the pay-go endpoint): qwen3.7-max
    # rejects images (400) -> text-only; qwen3.7-plus / qwen3.6-flash /
    # the VL series read red+blue correctly -> vision. qwen3.8-max /
    # qwen3.8-flash read red+blue correctly 6/6 each on the pay-go
    # endpoint (2026-11-10; give max_tokens room — max thinks first and
    # a 300-token budget can end before the answer) -> vision, matching
    # the Bailian console's 视觉理解 capability.
    'qwen': [
        {
            'id': 'qwen3.7-max',
            'label': 'Qwen3.7-Max (previous flagship)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'qwen3.8-max',
            'label': 'Qwen3.8-Max (flagship)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.8-flash',
            'label': 'Qwen3.8-Flash (fast)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.7-plus',
            'label': 'Qwen3.7-Plus',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.6-flash',
            'label': 'Qwen3.6-Flash (fast)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3-vl-plus',
            'label': 'Qwen3-VL-Plus',
            'vision': True,
        },
    ],
    'qwen_intl': [
        {
            'id': 'qwen3.7-max',
            'label': 'Qwen3.7-Max (previous flagship)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'qwen3.8-max',
            'label': 'Qwen3.8-Max (flagship)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.8-flash',
            'label': 'Qwen3.8-Flash (fast)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.7-plus',
            'label': 'Qwen3.7-Plus',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.6-flash',
            'label': 'Qwen3.6-Flash (fast)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3-vl-plus',
            'label': 'Qwen3-VL-Plus',
            'vision': True,
        },
    ],
    # Coding Plan serves qwen3.7-plus (same id verified as vision on
    # pay-go).
    'qwen_coding': [
        {
            'id': 'qwen3.7-plus',
            'label': 'Qwen3.7-Plus',
            'context': '1M',
            'vision': True,
        },
    ],
    # Token Plan flagship routes through ``auto`` (Alibaba's own
    # default); qwen3.8-max graduated from preview to GA. The 3.7/3.6
    # ids reuse the pay-go-verified labels. Probed on this endpoint
    # 2026-11-10 (red+blue, 3x): auto, qwen3.8-max, qwen3.8-flash and
    # qwen3.6-flash all 6/6 -> vision. The console lists auto as
    # text/reasoning only, but the router hands image requests to a
    # vision model. The international Token Plan is the same product and
    # router on a Singapore host, so its auto carries the same label: one
    # model, one capability, whichever region the key was bought in.
    'qwen_token': [
        {
            'id': 'auto',
            'label': 'Auto (provider-routed)',
            'vision': True,
        },
        {
            'id': 'qwen3.8-max',
            'label': 'Qwen3.8-Max (flagship)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.8-flash',
            'label': 'Qwen3.8-Flash (fast)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.7-max',
            'label': 'Qwen3.7-Max (previous flagship)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'qwen3.7-plus',
            'label': 'Qwen3.7-Plus',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.6-flash',
            'label': 'Qwen3.6-Flash (fast)',
            'context': '1M',
            'vision': True,
        },
    ],
    # MiMo: all three v2.6 chat models are 1M-context and accept image
    # input. Vision is DOC-verified (MiMo's image-understanding guide
    # lists ``mimo-v2.6-flash`` / ``mimo-v2.6-pro`` /
    # ``mimo-v2.6-pro-ultraspeed`` as supported, and the Anthropic
    # endpoint takes base64/url image blocks) — there is no MiMo key
    # here to live-probe, so this is NOT live-verified like the
    # GLM/MiniMax/DeepSeek entries. The ``[1m]`` suffix is MiMo's own
    # documented way to request the 1M window.
    # https://mimo.mi.com/docs/quick-start/usage-guide/multimodal-understanding/image-understanding
    'mimo': [
        {
            'id': 'mimo-v2.6-pro[1m]',
            'label': 'V2.6 Pro (1M context, flagship)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'mimo-v2.6-flash[1m]',
            'label': 'V2.6 Flash (1M context, cheap)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'mimo-v2.6-pro-ultraspeed[1m]',
            'label': 'V2.6 Pro Ultraspeed (1M context, fastest)',
            'context': '1M',
            'vision': True,
        },
    ],
    # The Token Plan serves the same catalog as pay-as-you-go.
    'mimo_token': [
        {
            'id': 'mimo-v2.6-pro[1m]',
            'label': 'V2.6 Pro (1M context, flagship)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'mimo-v2.6-flash[1m]',
            'label': 'V2.6 Flash (1M context, cheap)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'mimo-v2.6-pro-ultraspeed[1m]',
            'label': 'V2.6 Pro Ultraspeed (1M context, fastest)',
            'context': '1M',
            'vision': True,
        },
    ],
    # Regional / payment-path twins below: each serves the same catalog
    # as the preset it mirrors (only the host differs), so the entries
    # and their vision labels are copies of that preset's list. They
    # are literal copies rather than references because the presets
    # sync parses this file with ast.literal_eval.
    #
    # Kimi API open platform: ``kimi-`` prefixed ids. Vision is
    # DOC-verified from Moonshot's vision guide (kimi-k3.1-pro / kimi-k3
    # listed as natively multimodal), not live-probed — no key here.
    # kimi-k2.7-code is left out: it is thinking-only and 400s when
    # Claude Code sends a request with thinking off.
    'kimi_paygo': [
        {
            'id': 'kimi-k3[1m]',
            'label': 'K3 (1M context)',
            'context': '1M',
            'vision': True,
        },
        # K3.1 Pro / Highspeed are 1M models (1,048,576); the bare ids
        # get Claude Code's 200K default, so the [1m] ids come first.
        # Claude Code strips [1m] before the API call
        # (normalizeModelStringForAPI), so the endpoint sees the bare id.
        {
            'id': 'kimi-k3.1-pro[1m]',
            'label': 'K3.1 Pro (1M context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'kimi-k3.1-pro-highspeed[1m]',
            'label': 'K3.1 Pro Highspeed (1M context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'kimi-k3.1-pro',
            'label': 'K3.1 Pro (200K context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'kimi-k3.1-pro-highspeed',
            'label': 'K3.1 Pro Highspeed (200K context)',
            'context': '1M',
            'vision': True,
        },
    ],
    'kimi_paygo_intl': [
        {
            'id': 'kimi-k3[1m]',
            'label': 'K3 (1M context)',
            'context': '1M',
            'vision': True,
        },
        # K3.1 Pro / Highspeed are 1M models (1,048,576); the bare ids
        # get Claude Code's 200K default, so the [1m] ids come first.
        # Claude Code strips [1m] before the API call
        # (normalizeModelStringForAPI), so the endpoint sees the bare id.
        {
            'id': 'kimi-k3.1-pro[1m]',
            'label': 'K3.1 Pro (1M context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'kimi-k3.1-pro-highspeed[1m]',
            'label': 'K3.1 Pro Highspeed (1M context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'kimi-k3.1-pro',
            'label': 'K3.1 Pro (200K context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'kimi-k3.1-pro-highspeed',
            'label': 'K3.1 Pro Highspeed (200K context)',
            'context': '1M',
            'vision': True,
        },
    ],
    # Same models as api.minimax.cn (vision live-verified there).
    'minimax_intl': [
        {
            'id': 'MiniMax-M3.1-Flash[1m]',
            'label': 'M3.1 Flash (1M context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'MiniMax-M3[1m]',
            'label': 'M3 (1M context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'MiniMax-M3.1-Flash',
            'label': 'M3.1 Flash (200K context)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'MiniMax-M3',
            'label': 'M3 (200K context)',
            'context': '1M',
            'vision': True,
        },
    ],
    'qwen_coding_intl': [
        {
            'id': 'qwen3.7-plus',
            'label': 'Qwen3.7-Plus',
            'context': '1M',
            'vision': True,
        },
    ],
    'qwen_token_intl': [
        {
            'id': 'auto',
            'label': 'Auto (provider-routed)',
            'vision': True,
        },
        {
            'id': 'qwen3.8-max',
            'label': 'Qwen3.8-Max (flagship)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.8-flash',
            'label': 'Qwen3.8-Flash (fast)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.7-max',
            'label': 'Qwen3.7-Max (previous flagship)',
            'context': '1M',
            'vision': False,
        },
        {
            'id': 'qwen3.7-plus',
            'label': 'Qwen3.7-Plus',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'qwen3.6-flash',
            'label': 'Qwen3.6-Flash (fast)',
            'context': '1M',
            'vision': True,
        },
    ],
    'mimo_token_sgp': [
        {
            'id': 'mimo-v2.6-pro[1m]',
            'label': 'V2.6 Pro (1M context, flagship)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'mimo-v2.6-flash[1m]',
            'label': 'V2.6 Flash (1M context, cheap)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'mimo-v2.6-pro-ultraspeed[1m]',
            'label': 'V2.6 Pro Ultraspeed (1M context, fastest)',
            'context': '1M',
            'vision': True,
        },
    ],
    'mimo_token_ams': [
        {
            'id': 'mimo-v2.6-pro[1m]',
            'label': 'V2.6 Pro (1M context, flagship)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'mimo-v2.6-flash[1m]',
            'label': 'V2.6 Flash (1M context, cheap)',
            'context': '1M',
            'vision': True,
        },
        {
            'id': 'mimo-v2.6-pro-ultraspeed[1m]',
            'label': 'V2.6 Pro Ultraspeed (1M context, fastest)',
            'context': '1M',
            'vision': True,
        },
    ],
}
