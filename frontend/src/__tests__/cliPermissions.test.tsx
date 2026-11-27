/**
 * Settings → Integrations → WeCom: each permission's state.
 *
 * WeCom grants its bot each permission for 7 days, and the sign-in still
 * reads "authorized" after they expire. The row asks the server to check
 * each one (GET …/wecom/permissions) and, when any expired, says so in
 * the status line and turns "Sign in again" into the leading
 * "Re-authorize" — one scan renews them; each expired one also links
 * its own renewal.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'

const cli = enTranslation.integrations.cli

const mockApi = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
}))
vi.mock('../api', () => ({ api: mockApi }))

import { WorkplaceCliPanel } from '../components/settings/WorkplaceCliPanel'

const i18nTest = i18n.createInstance()
i18nTest.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

const WECOM = {
  key: 'wecom', title: 'WeCom', binary: 'wecom-cli', homepage: '',
  installed: true, version: '1.3.4', enabled: true,
  auth: { usable: true, reason: 'logged_in', identity: 'Bot aibEXAMPLE' },
  login: null, per_permission: true,
}
const RENEW = 'https://work.weixin.qq.com/ai/aiHelper/authorizationList?type=7'

function route(permissions: unknown, card: Record<string, unknown> = WECOM) {
  mockApi.get.mockImplementation(async (url: string) => {
    if (url === '/api/integrations/cli') return { integrations: [card] }
    if (url === '/api/integrations/cli/wecom/permissions') return { permissions }
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

describe('WeCom permissions on the integrations row', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('lists each permission and leads with Re-authorize when one expired', async () => {
    route([
      { key: 'doc', state: 'ok', renew_url: null },
      { key: 'mail', state: 'expired', renew_url: RENEW },
      { key: 'calendar', state: 'unknown', renew_url: null },
    ])
    renderPanel()
    const list = await screen.findByTestId('cli-permissions-wecom')
    expect(list.textContent).toContain(cli.permissions.name.doc)
    expect(screen.getByTestId('cli-permission-wecom-doc').dataset.state).toBe('ok')
    expect(screen.getByTestId('cli-permission-wecom-mail').textContent).toContain(cli.permissions.state.expired)
    expect(screen.getByTestId('cli-permission-wecom-calendar').dataset.state).toBe('unknown')
    // Only an expired one links its own renewal, opening in a new tab.
    const renew = screen.getByTestId('cli-permission-renew-wecom-mail') as HTMLAnchorElement
    expect(renew.href).toBe(RENEW)
    expect(renew.target).toBe('_blank')
    expect(screen.queryByTestId('cli-permission-renew-wecom-doc')).toBeNull()
    // The status line says it, in place of "the AI can use it".
    expect(screen.getByTestId('cli-permissions-expired-wecom').textContent).toBe(cli.permissions.someExpired)
    expect(screen.getByTestId('cli-status-wecom').textContent).not.toContain(cli.enabled)
    // The one scan that renews them all leads the row.
    const button = screen.getByTestId('cli-relogin-wecom')
    expect(button.textContent).toBe(cli.reauthorize)
    expect(button.className).toContain('bg-indigo-600')
    expect(screen.getByTestId('cli-permissions-hint-wecom').textContent).toContain(cli.permissions.expiredHint)
  })

  it('all granted: no hint, and the button stays "Sign in again"', async () => {
    route([
      { key: 'doc', state: 'ok', renew_url: null },
      { key: 'mail', state: 'ok', renew_url: null },
    ])
    renderPanel()
    await screen.findByTestId('cli-permissions-wecom')
    expect(screen.queryByTestId('cli-permissions-hint-wecom')).toBeNull()
    expect(screen.queryByTestId('cli-permissions-expired-wecom')).toBeNull()
    expect(screen.getByTestId('cli-status-wecom').textContent).toContain(cli.enabled)
    const button = screen.getByTestId('cli-relogin-wecom')
    expect(button.textContent).toBe(cli.relogin)
    expect(button.className).not.toContain('bg-indigo-600')
  })

  it('a whole sign-in that expired also leads with Re-authorize, and checks nothing', async () => {
    route(null, { ...WECOM, auth: { usable: false, reason: 'expired', identity: null } })
    renderPanel()
    const button = await screen.findByTestId('cli-relogin-wecom')
    expect(button.textContent).toBe(cli.reauthorize)
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith('/api/integrations/cli'))
    expect(mockApi.get).not.toHaveBeenCalledWith('/api/integrations/cli/wecom/permissions')
  })

  it('a platform that does not grant permissions one by one is never checked', async () => {
    route(null, { ...WECOM, key: 'feishu', per_permission: false })
    renderPanel()
    await screen.findByTestId('cli-row-feishu')
    expect(mockApi.get).not.toHaveBeenCalledWith('/api/integrations/cli/feishu/permissions')
    expect(screen.queryByTestId('cli-permissions-feishu')).toBeNull()
  })
})
