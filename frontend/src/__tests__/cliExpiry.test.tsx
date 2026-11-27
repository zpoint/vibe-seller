/** Settings → Integrations: one expired-authorization state, not a list
 * of individual capabilities. Expired → Re-authorize; otherwise the
 * usual Sign in again. Platforms never connected are not checked. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'

const cli = enTranslation.integrations.cli
const mockApi = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn(), patch: vi.fn(),
}))
vi.mock('../api', () => ({ api: mockApi }))

import { WorkplaceCliPanel } from '../components/settings/WorkplaceCliPanel'

const i18nTest = i18n.createInstance()
i18nTest.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en', fallbackLng: 'en', interpolation: { escapeValue: false },
})

const WECOM = {
  key: 'wecom', title: 'WeCom', binary: 'wecom-cli', homepage: '',
  installed: true, version: '1.3.4', enabled: true,
  auth: { usable: true, reason: 'logged_in', identity: 'Bot aibEXAMPLE' },
  login: null, checks_expiry: true,
}

function route(expired: boolean | null, card: Record<string, unknown> = WECOM) {
  mockApi.get.mockImplementation(async (url: string) => {
    if (url === '/api/integrations/cli') return { integrations: [card] }
    if (url === '/api/integrations/cli/wecom/expiry') return { expired }
    return undefined
  })
}

function renderPanel(isAdmin = true) {
  return render(
    <I18nextProvider i18n={i18nTest}>
      <WorkplaceCliPanel isAdmin={isAdmin} />
    </I18nextProvider>,
  )
}

describe('WeCom authorization expiry on the integrations row', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('expired: says so once and leads with Re-authorize', async () => {
    route(true)
    renderPanel()
    expect((await screen.findByTestId('cli-expired-wecom')).textContent).toBe(cli.expired)
    expect(screen.getByTestId('cli-expiry-hint-wecom').textContent).toContain(cli.expiredHint)
    const button = screen.getByTestId('cli-relogin-wecom')
    expect(button.textContent).toBe(cli.reauthorize)
    expect(button.className).toContain('bg-indigo-600')
    expect(screen.queryByTestId('cli-permissions-wecom')).toBeNull()
  })

  it('not expired: the usual enabled state and Sign in again', async () => {
    route(false)
    renderPanel()
    await screen.findByTestId('cli-row-wecom')
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith('/api/integrations/cli/wecom/expiry'))
    expect(screen.queryByTestId('cli-expired-wecom')).toBeNull()
    expect(screen.queryByTestId('cli-expiry-hint-wecom')).toBeNull()
    expect(screen.getByTestId('cli-status-wecom').textContent).toContain(cli.enabled)
    const button = screen.getByTestId('cli-relogin-wecom')
    expect(button.textContent).toBe(cli.relogin)
    expect(button.className).not.toContain('bg-indigo-600')
  })

  it('a whole sign-in that expired also leads with Re-authorize, without another check', async () => {
    route(null, { ...WECOM, auth: { usable: false, reason: 'expired', identity: null } })
    renderPanel()
    const button = await screen.findByTestId('cli-relogin-wecom')
    expect(button.textContent).toBe(cli.reauthorize)
    expect(mockApi.get).not.toHaveBeenCalledWith('/api/integrations/cli/wecom/expiry')
  })

  for (const key of ['feishu', 'dingtalk'] as const) {
    it(`${key}: an enabled sign-in that expired shows the same Re-authorize UI`, async () => {
      route(null, {
        ...WECOM, key, title: key, checks_expiry: false,
        auth: { usable: false, reason: 'expired', identity: null },
      })
      renderPanel()
      const button = await screen.findByTestId(`cli-relogin-${key}`)
      expect(button.textContent).toBe(cli.reauthorize)
      expect(button.className).toContain('bg-indigo-600')
      expect(screen.getByTestId(`cli-needs-signin-${key}`)).toBeTruthy()
      expect(mockApi.get).not.toHaveBeenCalledWith(`/api/integrations/cli/${key}/expiry`)
    })

    it(`${key}: downloaded but never connected does not offer re-authorization`, async () => {
      route(null, { ...WECOM, key, title: key, enabled: false, auth: null, checks_expiry: false })
      renderPanel()
      await screen.findByTestId(`cli-connect-${key}`)
      expect(screen.queryByTestId(`cli-relogin-${key}`)).toBeNull()
      expect(screen.queryByTestId(`cli-needs-signin-${key}`)).toBeNull()
    })
  }

  it('a platform without the online expiry check is never queried', async () => {
    route(null, { ...WECOM, key: 'feishu', checks_expiry: false })
    renderPanel()
    await screen.findByTestId('cli-row-feishu')
    expect(mockApi.get).not.toHaveBeenCalledWith('/api/integrations/cli/feishu/expiry')
    expect(screen.queryByTestId('cli-expired-feishu')).toBeNull()
  })
})
