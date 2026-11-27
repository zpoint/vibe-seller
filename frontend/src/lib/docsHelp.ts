// Where the user docs explain how to fix each failure the app shows.
//
// One entry per failure, in both languages: the docs site serves the
// Chinese page at the root and the English one under /en/, and a
// heading's anchor is its own text (so the two differ). The URLs are
// written out in full on purpose: tests/unit/test_docs_links.py finds
// every docs.vibe-sellers.com link in the app and checks that its page
// and anchor exist in docs/, so a renamed heading fails CI instead of
// sending a user to the top of a page.
//
// An FAQ entry inside <details> is addressed by the id on its <details>
// tag; the docs site opens the entry when the link lands on it.

export interface DocsHelp {
  zh: string
  en: string
}

export const DOCS_HELP = {
  // ── Ziniao (store setup, task errors) ──
  ziniaoNoPermission: {
    zh: 'https://docs.vibe-sellers.com/ziniao/#报错无权限通过-webdriver-登录',
    en: 'https://docs.vibe-sellers.com/en/ziniao/#无权限通过-webdriver-登录-when-binding-a-store',
  },
  ziniaoNewTerminal: {
    zh: 'https://docs.vibe-sellers.com/ziniao/#报错检测到您在新终端登录',
    en: 'https://docs.vibe-sellers.com/en/ziniao/#检测到您在新终端登录-when-binding-a-store',
  },
  ziniaoCredentials: {
    zh: 'https://docs.vibe-sellers.com/ziniao/#凭据存在哪里',
    en: 'https://docs.vibe-sellers.com/en/ziniao/#where-the-credentials-are-stored',
  },
  /** WSL: Ziniao is started on the Windows side by ziniao_webdriver.bat. */
  ziniaoWslLauncher: {
    zh: 'https://docs.vibe-sellers.com/ziniao/#windowswsl让紫鸟进入开发者模式ziniao_webdriverbat',
    en: 'https://docs.vibe-sellers.com/en/ziniao/#windows-wsl-getting-ziniao-into-developer-mode-ziniao_webdriverbat',
  },
  /** Developer mode, and how Vibe Seller restarts Ziniao into it. */
  ziniaoDeveloperMode: {
    zh: 'https://docs.vibe-sellers.com/ziniao/#开通前提开发者模式与-webdriver-登录',
    en: 'https://docs.vibe-sellers.com/en/ziniao/#prerequisites-developer-mode-and-webdriver-login',
  },
  /** Any other Ziniao failure: the page of Ziniao setup and errors. */
  ziniaoPage: {
    zh: 'https://docs.vibe-sellers.com/ziniao/',
    en: 'https://docs.vibe-sellers.com/en/ziniao/',
  },

  // ── Yuehulian (store setup) ──
  yuehulianErrors: {
    zh: 'https://docs.vibe-sellers.com/yuehulian/#常见提示--报错',
    en: 'https://docs.vibe-sellers.com/en/yuehulian/#common-statuses--errors',
  },
  yuehulianOneAccount: {
    zh: 'https://docs.vibe-sellers.com/yuehulian/#一台机器只支持一个账号',
    en: 'https://docs.vibe-sellers.com/en/yuehulian/#one-account-per-machine',
  },

  // ── Browser kernels (task errors) ──
  /** A store on a Firefox / non-Chrome kernel: UNSUPPORTED_KERNEL. */
  browserKernel: {
    zh: 'https://docs.vibe-sellers.com/browsers/#店铺要用-chrome-内核',
    en: 'https://docs.vibe-sellers.com/en/browsers/#stores-must-use-a-chrome-kernel',
  },

  // ── AI models (task errors) ──
  aiAuthFailed: {
    zh: 'https://docs.vibe-sellers.com/llm-keys/#auth-failed',
    en: 'https://docs.vibe-sellers.com/en/llm-keys/#auth-failed',
  },
  aiBalanceOrQuota: {
    zh: 'https://docs.vibe-sellers.com/llm-keys/#balance-or-quota',
    en: 'https://docs.vibe-sellers.com/en/llm-keys/#balance-or-quota',
  },
  aiFaq: {
    zh: 'https://docs.vibe-sellers.com/llm-keys/#常见问题',
    en: 'https://docs.vibe-sellers.com/en/llm-keys/#faq',
  },

  // ── Email ──
  emailLoginFailed: {
    zh: 'https://docs.vibe-sellers.com/email/#test-connection-failed',
    en: 'https://docs.vibe-sellers.com/en/email/#test-connection-failed',
  },
  emailAlreadyAdded: {
    zh: 'https://docs.vibe-sellers.com/email/#already-added',
    en: 'https://docs.vibe-sellers.com/en/email/#already-added',
  },
  emailServersNotDetected: {
    zh: 'https://docs.vibe-sellers.com/email/#servers-not-detected',
    en: 'https://docs.vibe-sellers.com/en/email/#servers-not-detected',
  },

  // ── Amazon Ads API ──
  adsServiceUnreachable: {
    zh: 'https://docs.vibe-sellers.com/ads-api/#ads-service-unreachable',
    en: 'https://docs.vibe-sellers.com/en/ads-api/#ads-service-unreachable',
  },
  adsNotYetAuthorized: {
    zh: 'https://docs.vibe-sellers.com/ads-api/#not-yet-authorized',
    en: 'https://docs.vibe-sellers.com/en/ads-api/#not-yet-authorized',
  },
  /** Ziniao refuses to show the consent page until its domain is allowlisted. */
  adsZiniaoBlocked: {
    zh: 'https://docs.vibe-sellers.com/ads-api/#ziniao-blocked',
    en: 'https://docs.vibe-sellers.com/en/ads-api/#ziniao-blocked',
  },

  // ── WeCom / Feishu / DingTalk / Google Workspace ──
  cliDownloadFailed: {
    zh: 'https://docs.vibe-sellers.com/workplace-cli/#download-failed',
    en: 'https://docs.vibe-sellers.com/en/workplace-cli/#download-failed',
  },
  cliDoesNotRun: {
    zh: 'https://docs.vibe-sellers.com/workplace-cli-wecom/#does-not-run',
    en: 'https://docs.vibe-sellers.com/en/workplace-cli-wecom/#does-not-run',
  },
  feishuMissingScopes: {
    zh: 'https://docs.vibe-sellers.com/workplace-cli-feishu/#admin-restricted-permissions',
    en: 'https://docs.vibe-sellers.com/en/workplace-cli-feishu/#admin-restricted-permissions',
  },
  feishuNoRefreshToken: {
    zh: 'https://docs.vibe-sellers.com/workplace-cli-feishu/#no-refresh-token',
    en: 'https://docs.vibe-sellers.com/en/workplace-cli-feishu/#no-refresh-token',
  },
  dingtalkCliAccess: {
    zh: 'https://docs.vibe-sellers.com/workplace-cli-dingtalk/#admin',
    en: 'https://docs.vibe-sellers.com/en/workplace-cli-dingtalk/#admin',
  },
  googleAccessBlocked: {
    zh: 'https://docs.vibe-sellers.com/google-workspace/#access-blocked',
    en: 'https://docs.vibe-sellers.com/en/google-workspace/#access-blocked',
  },
  googleSignInExpired: {
    zh: 'https://docs.vibe-sellers.com/google-workspace/#sign-in-expired',
    en: 'https://docs.vibe-sellers.com/en/google-workspace/#sign-in-expired',
  },
  googleWebClient: {
    zh: 'https://docs.vibe-sellers.com/google-workspace/#key-for-a-website',
    en: 'https://docs.vibe-sellers.com/en/google-workspace/#key-for-a-website',
  },
  googleSignInKey: {
    zh: 'https://docs.vibe-sellers.com/google-workspace/#2-创建你的-google-登录密钥只做一次',
    en: 'https://docs.vibe-sellers.com/en/google-workspace/#2-create-your-google-sign-in-key-once',
  },

  // ── Custom MCP servers ──
  mcpTokenInvalid: {
    zh: 'https://docs.vibe-sellers.com/mcp-custom/#token-invalid',
    en: 'https://docs.vibe-sellers.com/en/mcp-custom/#token-invalid',
  },
  mcpSampleHeader: {
    zh: 'https://docs.vibe-sellers.com/mcp-custom/#sample-header',
    en: 'https://docs.vibe-sellers.com/en/mcp-custom/#sample-header',
  },
  mcpOauth: {
    zh: 'https://docs.vibe-sellers.com/mcp-custom/#oauth-sign-in',
    en: 'https://docs.vibe-sellers.com/en/mcp-custom/#oauth-sign-in',
  },
  mcpVariable: {
    zh: 'https://docs.vibe-sellers.com/mcp-custom/#config-variable',
    en: 'https://docs.vibe-sellers.com/en/mcp-custom/#config-variable',
  },

  // ── Chat bots ──
  chatBotTestFailed: {
    zh: 'https://docs.vibe-sellers.com/chat-bots/#test-errors',
    en: 'https://docs.vibe-sellers.com/en/chat-bots/#test-errors',
  },

  // ── Sign-in ──
  loginPassword: {
    zh: 'https://docs.vibe-sellers.com/faq-login/#登录密码怎么设置和找回',
    en: 'https://docs.vibe-sellers.com/en/faq-login/#how-do-i-set-up-or-recover-the-login-password',
  },
} as const satisfies Record<string, DocsHelp>

export type DocsHelpTopic = keyof typeof DOCS_HELP

/** The docs link for *topic*, in the UI's language (English unless the
 *  UI is Chinese — the same rule as the AI setup guide's link). */
export function docsHelpUrl(topic: DocsHelpTopic, language: string | undefined): string {
  const entry: DocsHelp = DOCS_HELP[topic]
  return (language || 'en').startsWith('zh') ? entry.zh : entry.en
}

// Which failure is which topic, per screen. A failure missing here has
// no section in the docs yet.

/** Workplace CLI / Google Workspace errors (``integrations.cli.error.<code>``). */
export const CLI_ERROR_HELP: Partial<Record<string, DocsHelpTopic>> = {
  download_failed: 'cliDownloadFailed',
  does_not_run: 'cliDoesNotRun',
  google_client_web: 'googleWebClient',
  google_client_invalid: 'googleSignInKey',
}

/** Workplace CLI notices our docs explain (``integrations.cli.notice.<key>``);
 *  feishu_missing_scopes links its docs entry as the notice's own guide. */
export const CLI_NOTICE_HELP: Partial<Record<string, DocsHelpTopic>> = {
  feishu_no_refresh_token: 'feishuNoRefreshToken',
  dingtalk_cli_access: 'dingtalkCliAccess',
}

/** Custom MCP: a save refused with a ``config_*`` code (app/custom_mcp.py). */
export const MCP_CONFIG_ERROR_HELP: Partial<Record<string, DocsHelpTopic>> = {
  config_placeholder: 'mcpSampleHeader',
  config_masked: 'mcpSampleHeader',
  config_variable: 'mcpVariable',
  config_oauth: 'mcpOauth',
}

/** Custom MCP: a connection that failed for *reason* (app/custom_mcp_probe.py). */
export const MCP_REASON_HELP: Partial<Record<string, DocsHelpTopic>> = {
  unauthorized: 'mcpTokenInvalid',
}

/** A failed task's error categories (app/errors.py) that the docs explain. */
const TASK_ERROR_HELP: Partial<Record<string, DocsHelpTopic>> = {
  auth_failed: 'aiAuthFailed',
  billing_error: 'aiBalanceOrQuota',
  rate_limit: 'aiBalanceOrQuota',
  invalid_request: 'aiFaq',
}

/** The docs section for a failed task's error, or null when the docs
 *  have nothing on it. A Ziniao that WSL could not reach is said in the
 *  error text itself (it links the launcher download). */
export function taskErrorHelp(category: string | null | undefined, error: string): DocsHelpTopic | null {
  const topic = category ? TASK_ERROR_HELP[category] : undefined
  if (topic) return topic
  if (error.includes('/api/ziniao/launcher')) return 'ziniaoWslLauncher'
  // A store on a Firefox / other non-Chrome kernel: keyed on the
  // STABLE taxonomy code the error surfaces (UnsupportedKernel prefixes
  // it to its text), never on translatable prose.
  if (error.includes('UNSUPPORTED_KERNEL')) return 'browserKernel'
  return null
}
