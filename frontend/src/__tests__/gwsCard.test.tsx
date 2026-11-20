/**
 * Settings → Integrations → Google Workspace: its own card, the same
 * rows as WeCom / Feishu / DingTalk, plus what Google needs —
 * the one-time sign-in key (a downloaded file) before the first
 * sign-in, and a box for the address a browser on another computer
 * ends on after "Allow".
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'

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

function gws(over: Record<string, unknown> = {}) {
  return {
    key: 'gws',
    title: 'Google Workspace',
    binary: 'gws',
    homepage: '',
    installed: false,
    version: null,
    enabled: false,
    auth: null,
    login: null,
    setup: 'google_client',
    needs_setup: false,
    notice: null,
    ...over,
  }
}

const NEEDS_SETUP = {
  installed: true,
  version: '0.22.5',
  auth: { usable: false, reason: 'not_configured', identity: null },
  needs_setup: true,
}

function renderCards(google: boolean) {
  return render(
    <I18nextProvider i18n={i18nTest}>
      <WorkplaceCliPanel isAdmin google={google} />
    </I18nextProvider>,
  )
}

function list(...cards: Record<string, unknown>[]) {
  return { integrations: cards }
}

describe('Google Workspace card', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('has its own card; the workplace card does not show it', async () => {
    const both = list(
      gws(),
      { ...gws(), key: 'wecom', title: 'WECOM', setup: null },
    )
    mockApi.get.mockResolvedValue(both)
    const { unmount } = renderCards(true)
    expect(await screen.findByTestId('gws-card')).toBeTruthy()
    expect(await screen.findByTestId('cli-row-gws')).toBeTruthy()
    expect(screen.queryByTestId('cli-row-wecom')).toBeNull()
    unmount()
    renderCards(false)
    expect(await screen.findByTestId('cli-row-wecom')).toBeTruthy()
    expect(screen.queryByTestId('cli-row-gws')).toBeNull()
  })

  it('Connect downloads, then asks for the sign-in key before any sign-in', async () => {
    let installed = false
    mockApi.get.mockImplementation(() =>
      Promise.resolve(list(installed ? gws(NEEDS_SETUP) : gws())),
    )
    mockApi.post.mockImplementation((url: string) => {
      if (url.endsWith('/install')) installed = true
      return Promise.resolve({ ok: true })
    })
    renderCards(true)
    fireEvent.click(await screen.findByTestId('cli-connect-gws'))

    const form = await screen.findByTestId('cli-setup-gws')
    expect(form.textContent).toContain(cli.setup.google_client.title)
    const steps = screen.getByTestId('cli-setup-steps-gws').querySelectorAll('li')
    expect(steps).toHaveLength(cli.setup.google_client.steps.length)
    // Every step done in Google Cloud has its link.
    const links = form.querySelectorAll('a[href^="https://console.cloud.google.com/"]')
    expect(links.length).toBe(5)
    const posted = mockApi.post.mock.calls.map((c) => c[0])
    expect(posted).toEqual(['/api/integrations/cli/gws/install'])
  })

  it('the chosen key file is sent, then the sign-in starts', async () => {
    mockApi.get.mockResolvedValue(list(gws(NEEDS_SETUP)))
    mockApi.post.mockImplementation((url: string) => {
      if (url.endsWith('/login')) {
        return Promise.resolve({ state: 'starting', steps: 1, step: 1 })
      }
      return Promise.resolve({ ok: true })
    })
    renderCards(true)
    fireEvent.click(await screen.findByTestId('cli-connect-gws'))
    const input = (await screen.findByTestId('cli-setup-file-gws')) as HTMLInputElement
    const file = new File(['{"installed": {"client_id": "x"}}'], 'client_secret_1.json', {
      type: 'application/json',
    })
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() =>
      expect(mockApi.post).toHaveBeenCalledWith('/api/integrations/cli/gws/setup', {
        payload: '{"installed": {"client_id": "x"}}',
      }),
    )
    await waitFor(() =>
      expect(mockApi.post).toHaveBeenCalledWith('/api/integrations/cli/gws/login', {
        enable_after: true,
      }),
    )
  })

  it('a key for a website is refused with what to do instead', async () => {
    mockApi.get.mockResolvedValue(list(gws(NEEDS_SETUP)))
    mockApi.post.mockImplementation((url: string) => {
      if (url.endsWith('/setup')) {
        return Promise.reject({
          detail: { code: 'google_client_web', message: 'a Web application client' },
        })
      }
      return Promise.resolve({ ok: true })
    })
    renderCards(true)
    fireEvent.click(await screen.findByTestId('cli-connect-gws'))
    const input = await screen.findByTestId('cli-setup-file-gws')
    fireEvent.change(input, {
      target: { files: [new File(['{"web": {}}'], 'k.json')] },
    })
    const err = await screen.findByTestId('cli-error')
    expect(err.textContent).toContain(cli.error.google_client_web)
    expect(mockApi.post).not.toHaveBeenCalledWith(
      '/api/integrations/cli/gws/login',
      expect.anything(),
    )
  })

  it('a waiting sign-in offers the paste box and relays the address', async () => {
    const waiting = {
      state: 'waiting',
      url: `https://accounts.google.com/o/oauth2/auth?scope=${'s%20'.repeat(80)}`,
      user_code: null,
      qr: false,
      message: null,
      step: 1,
      steps: 1,
      hint: 'gws_auth',
      paste_back: true,
      callback: 'http://localhost:53969',
    }
    mockApi.get.mockImplementation((url: string) =>
      Promise.resolve(
        url.endsWith('/login')
          ? waiting
          : list(gws({ ...NEEDS_SETUP, needs_setup: false, login: waiting })),
      ),
    )
    mockApi.post.mockResolvedValue({ ok: true })
    renderCards(true)

    const box = await screen.findByTestId('cli-paste-gws')
    expect(box.textContent).toContain('http://localhost:53969')
    expect(screen.getByTestId('cli-login-url-gws').getAttribute('href')).toBe(waiting.url)
    // Google's link runs to hundreds of characters: the page prints its
    // site, the link itself is whole.
    expect(screen.getByTestId('cli-login-url-gws').textContent).toBe(
      'https://accounts.google.com/…',
    )
    expect(screen.getByTestId('cli-login-gws').textContent).toContain(cli.hint.gws_auth)

    const address = 'http://localhost:53969/?code=4/abc&scope=x'
    fireEvent.change(screen.getByTestId('cli-paste-input-gws'), {
      target: { value: address },
    })
    fireEvent.click(screen.getByTestId('cli-paste-submit-gws'))
    await waitFor(() =>
      expect(mockApi.post).toHaveBeenCalledWith('/api/integrations/cli/gws/login/paste', {
        address,
      }),
    )
  })

  it('an expired sign-in says so, and that the AI cannot use it until signed in again', async () => {
    mockApi.get.mockResolvedValue(
      list(
        gws({
          installed: true,
          version: '0.22.5',
          enabled: true,
          auth: { usable: false, reason: 'expired', identity: null },
        }),
      ),
    )
    renderCards(true)
    const status = await screen.findByTestId('cli-status-gws')
    expect(status.textContent).toContain(cli.signInExpired)
    expect(status.textContent).toContain(cli.enabledNeedsSignIn)
    expect(status.textContent).not.toContain(cli.enabled)
    expect(screen.getByTestId('cli-relogin-gws')).toBeTruthy()
  })

  it('after the upgrade, the row says the earlier switch moved here', async () => {
    mockApi.get.mockResolvedValue(list(gws({ notice: 'gws_moved' })))
    renderCards(true)
    const note = await screen.findByTestId('cli-row-notice-gws')
    expect(note.textContent).toBe(cli.rowNotice.gws_moved)
  })

  it('zh and en have the same Google Workspace keys', () => {
    const keys = (o: unknown, p = ''): string[] =>
      o && typeof o === 'object' && !Array.isArray(o)
        ? Object.entries(o).flatMap(([k, v]) => keys(v, `${p}.${k}`))
        : [p]
    const pick = (t: typeof enTranslation) =>
      [
        ...keys(t.integrations.gws, 'gws'),
        ...keys(t.integrations.cli.setup, 'setup'),
        ...keys(t.integrations.cli.pasteBack, 'pasteBack'),
        ...keys(t.integrations.cli.rowNotice, 'rowNotice'),
        ...keys(t.integrations.cli.error, 'error'),
      ].sort()
    expect(pick(zhTranslation as typeof enTranslation)).toEqual(pick(enTranslation))
    expect(zhTranslation.integrations.cli.setup.google_client.steps).toHaveLength(
      enTranslation.integrations.cli.setup.google_client.steps.length,
    )
  })
})
