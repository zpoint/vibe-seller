/**
 * Every failure the docs explain links its docs section, in the UI's
 * language (lib/docsHelp.ts, components/ui/DocsHelpLink.tsx).
 *
 * The case that started it: binding a Ziniao store whose sub-account has
 * no WebDriver permission. Ziniao answers "无权限通过webdriver登录" and the
 * box showed only that text and Refresh — the guide was hidden whenever
 * Ziniao's own text was there. The fix is the same either way, so the box
 * always offers the guide and the docs section.
 *
 * That each link's page and anchor exist in docs/ is
 * tests/unit/test_docs_links.py.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { ZiniaoSection, YuehulianSection } from '../components/StorePickers'
import { TaskErrorNote } from '../components/TaskErrorNote'
import { DOCS_HELP, docsHelpUrl, taskErrorHelp, type DocsHelpTopic } from '../lib/docsHelp'

vi.mock('../api', () => ({ api: { get: vi.fn(async () => ({ configured: true, team_name: 'acme', subaccount: 'ops' })), post: vi.fn() } }))

function i18nFor(lng: 'en' | 'zh') {
  const inst = i18n.createInstance()
  inst.use(initReactI18next).init({
    resources: { en: { translation: enTranslation }, zh: { translation: zhTranslation } },
    lng, fallbackLng: 'en', interpolation: { escapeValue: false },
  })
  return inst
}

function renderZiniao(over: Partial<Parameters<typeof ZiniaoSection>[0]>, lng: 'en' | 'zh' = 'en') {
  const props = {
    ziniaoAccounts: [{ id: 'acct-1', name: 'Acme', company: 'acme', username: 'ops' }],
    selectedZiniaoAccountId: 'acct-1', setSelectedZiniaoAccountId: vi.fn(),
    ziniaoBrowsers: [], selectedBrowserOauth: '', setSelectedBrowserOauth: vi.fn(),
    fetchingBrowsers: false, browserFetchError: '', setBrowserFetchError: vi.fn(),
    fetchBrowserProfiles: vi.fn(), ziniaoStartingSecs: 0, serverPlatform: 'windows',
    showAddAccount: false, setShowAddAccount: vi.fn(),
    showAccountPassword: false, setShowAccountPassword: vi.fn(),
    editingAccountId: '', setEditingAccountId: vi.fn(),
    newAccount: { name: '', company: '', username: '', password: '' }, setNewAccount: vi.fn(),
    createZiniaoAccount: vi.fn(), updateZiniaoAccount: vi.fn(), deleteZiniaoAccount: vi.fn(),
    isAdmin: false,
    ...over,
  } as Parameters<typeof ZiniaoSection>[0]
  return render(
    <I18nextProvider i18n={i18nFor(lng)}>
      <ZiniaoSection {...props} />
    </I18nextProvider>,
  )
}

/** Ziniao's own error text, as fetchBrowserProfiles encodes it. */
const ziniaoText = (s: string) => btoa(unescape(encodeURIComponent(s)))

const helpLink = () => screen.getByTestId('docs-help-link') as HTMLAnchorElement

describe('Ziniao store setup', () => {
  it('offers the guide and the docs when Ziniao says the account has no WebDriver permission', () => {
    renderZiniao({ browserFetchError: `ziniao:no_permission:${ziniaoText('无权限通过webdriver登录')}` }, 'zh')
    expect(screen.getByText('无权限通过webdriver登录')).toBeTruthy()
    expect(screen.getByText(zhTranslation.settings.ziniaoEnableWebDriver)).toBeTruthy()
    expect(helpLink().href).toBe(new URL(DOCS_HELP.ziniaoNoPermission.zh).href)
    expect(helpLink().textContent).toContain(zhTranslation.common.docsHelp)
    expect(helpLink().target).toBe('_blank')
  })

  it.each<[string, 'windows' | 'mac' | 'wsl', DocsHelpTopic]>([
    ['ziniao:no_permission', 'windows', 'ziniaoNoPermission'],
    ['ziniao:new_terminal_login', 'windows', 'ziniaoNewTerminal'],
    ['ziniao:credentials_error', 'windows', 'ziniaoCredentials'],
    ['ziniao:not_installed', 'mac', 'ziniaoPage'],
    ['ziniao:start_failed', 'windows', 'ziniaoDeveloperMode'],
    ['ziniao:account_conflict', 'windows', 'ziniaoPage'],
    ['connect_error', 'wsl', 'ziniaoWslLauncher'],
    ['ziniao:not_running', 'wsl', 'ziniaoWslLauncher'],
    ['connect_error', 'mac', 'ziniaoDeveloperMode'],
    ['connect_error', 'windows', 'ziniaoDeveloperMode'],
    ['api_error:Ziniao API error: wrong company', 'windows', 'ziniaoPage'],
  ])('links the docs for %s on %s', (error, platform, topic) => {
    renderZiniao({ browserFetchError: error, serverPlatform: platform })
    expect(helpLink().dataset.topic).toBe(topic)
    expect(helpLink().href).toBe(new URL(DOCS_HELP[topic].en).href)
  })

  it('links nothing while the store list loads or when it is just empty', () => {
    renderZiniao({ fetchingBrowsers: true })
    expect(screen.queryByTestId('docs-help-link')).toBeNull()
  })
})

describe('Yuehulian store setup', () => {
  it('links the common errors section next to the status', () => {
    render(
      <I18nextProvider i18n={i18nFor('en')}>
        <YuehulianSection
          selectedBrowserOauth="" setSelectedBrowserOauth={vi.fn()}
          yuehulianStores={[]} yuehulianError="yuehulian:client_not_running"
          fetchingYuehulian={false} fetchYuehulianStores={vi.fn()} stores={[]}
        />
      </I18nextProvider>,
    )
    expect(screen.getByText(enTranslation.settings.yuehulianClientNotRunning)).toBeTruthy()
    expect(helpLink().dataset.topic).toBe('yuehulianErrors')
  })
})

describe('failed task', () => {
  const renderNote = (error: string, category: string | null) => render(
    <I18nextProvider i18n={i18nFor('en')}>
      <TaskErrorNote error={error} category={category} />
    </I18nextProvider>,
  )

  it.each<[string | null, string, DocsHelpTopic]>([
    ['auth_failed', 'invalid x-api-key', 'aiAuthFailed'],
    ['billing_error', 'insufficient credit', 'aiBalanceOrQuota'],
    ['rate_limit', '429', 'aiBalanceOrQuota'],
    ['invalid_request', 'model not found', 'aiFaq'],
    [null, 'Ziniao is not running. Download /api/ziniao/launcher and run it.', 'ziniaoWslLauncher'],
    // UNSUPPORTED_KERNEL keys on the stable taxonomy code the error
    // surfaces (UnsupportedKernel prefixes it), not on prose.
    [null, '[UNSUPPORTED_KERNEL] Failed to start browser: 这个店铺用的不是 Chrome 内核', 'browserKernel'],
    [null, 'Failed to start browser: [UNSUPPORTED_KERNEL] vendor message here', 'browserKernel'],
    // A terminal browser-start failure records the taxonomy code
    // (snake-cased) as the category; every terminal category links
    // its browsers-page entry.
    ['unsupported_kernel', '[UNSUPPORTED_KERNEL] kernel is Firefox', 'browserKernel'],
    ['login_required', '[LOGIN_REQUIRED] client signed out', 'browserLoginRequired'],
    ['client_not_running', '[CLIENT_NOT_RUNNING] not up', 'browserClientNotRunning'],
    ['account_mismatch', '[ACCOUNT_MISMATCH] other account', 'browserAccountMismatch'],
    ['api_not_enabled', '[API_NOT_ENABLED] api off', 'browserApiNotEnabled'],
    ['plan_required', '[PLAN_REQUIRED] needs a plan', 'browserPlanRequired'],
    ['store_unavailable', '[STORE_UNAVAILABLE] no device bound', 'browserStoreUnavailable'],
    ['store_not_found', '[STORE_NOT_FOUND] gone', 'browserStoreNotFound'],
  ])('links the docs for %s', (category, error, topic) => {
    renderNote(error, category)
    expect(helpLink().dataset.topic).toBe(topic)
  })

  it('shows the plain-language sentence for a terminal browser-start category', () => {
    // The banner renders the i18n sentence for the category, with the
    // raw error as fallback — the user never sees the bare code only.
    renderNote('[UNSUPPORTED_KERNEL] kernel is Firefox', 'unsupported_kernel')
    expect(screen.getByText(enTranslation.tasks.error_unsupported_kernel)).toBeTruthy()
    expect(helpLink().dataset.topic).toBe('browserKernel')
  })

  it('links nothing for a failure the docs do not cover', () => {
    renderNote('the agent looped', 'agent_loop')
    expect(screen.getByText(enTranslation.tasks.error_agent_loop)).toBeTruthy()
    expect(screen.queryByTestId('docs-help-link')).toBeNull()
    expect(taskErrorHelp(null, 'something else')).toBeNull()
  })

  it('does not link the kernel topic for kernel-ish prose without the token', () => {
    // Negative control: prose alone (no UNSUPPORTED_KERNEL token) must
    // NOT link — the mapping is keyed on the stable code, not on words
    // like "Chrome kernel" that translations vary.
    expect(taskErrorHelp(null, 'Failed to start browser: uses a Chrome kernel, still no link')).toBeNull()
  })
})

describe('docsHelpUrl', () => {
  it('uses the Chinese page for a Chinese UI and the English one otherwise', () => {
    expect(docsHelpUrl('aiAuthFailed', 'zh')).toBe(DOCS_HELP.aiAuthFailed.zh)
    expect(docsHelpUrl('aiAuthFailed', 'zh-CN')).toBe(DOCS_HELP.aiAuthFailed.zh)
    expect(docsHelpUrl('aiAuthFailed', 'en')).toBe(DOCS_HELP.aiAuthFailed.en)
    expect(docsHelpUrl('aiAuthFailed', undefined)).toBe(DOCS_HELP.aiAuthFailed.en)
  })

  it('points each language at its own site', () => {
    for (const [topic, { zh, en }] of Object.entries(DOCS_HELP)) {
      expect(zh, topic).toMatch(/^https:\/\/docs\.vibe-sellers\.com\/(?!en\/)/)
      expect(en, topic).toMatch(/^https:\/\/docs\.vibe-sellers\.com\/en\//)
    }
  })
})
