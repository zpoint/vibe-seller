/**
 * Settings → Integrations → workplace CLIs (WeCom / Feishu / DingTalk).
 *
 * The page only drives the backend: Connect = install, then a login
 * whose link / code / QR the page shows from the polled view, with
 * `enable_after` so the server finishes the job even if the page is
 * closed. Only admins get the buttons.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'

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

function card(key: string, over: Record<string, unknown> = {}) {
  return {
    key,
    title: key.toUpperCase(),
    binary: `${key}-cli`,
    homepage: '',
    installed: false,
    version: null,
    enabled: false,
    auth: null,
    login: null,
    ...over,
  }
}

function renderPanel(isAdmin: boolean) {
  return render(
    <I18nextProvider i18n={i18nTest}>
      <WorkplaceCliPanel isAdmin={isAdmin} />
    </I18nextProvider>,
  )
}

describe('WorkplaceCliPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('lists every platform; members see state but no buttons', async () => {
    mockApi.get.mockResolvedValue({
      integrations: [
        card('wecom'),
        card('feishu', {
          installed: true,
          version: '1.0.100',
          enabled: true,
          auth: { usable: true, reason: 'logged_in', identity: 'Alice' },
        }),
        card('dingtalk'),
      ],
    })
    renderPanel(false)
    expect(await screen.findByTestId('cli-row-wecom')).toBeTruthy()
    expect(screen.getByTestId('cli-row-dingtalk')).toBeTruthy()
    const feishu = screen.getByTestId('cli-status-feishu').textContent ?? ''
    expect(feishu).toContain('1.0.100')
    expect(feishu).toContain('Alice')
    expect(feishu).toContain(enTranslation.integrations.cli.enabled)
    expect(screen.queryByTestId('cli-connect-wecom')).toBeNull()
    expect(screen.queryByTestId('cli-disable-feishu')).toBeNull()
  })

  it('survives a list response without integrations', async () => {
    mockApi.get.mockResolvedValue([])
    renderPanel(true)
    expect(await screen.findByTestId('cli-card')).toBeTruthy()
    expect(screen.queryByTestId('cli-row-wecom')).toBeNull()
  })

  it('Connect installs, starts an enable-after login and shows its link + code', async () => {
    const view = {
      state: 'waiting',
      url: 'https://login.example/device',
      user_code: 'ABCD-1234',
      qr: false,
      message: null,
      step: 1,
      steps: 1,
      hint: 'dingtalk_auth',
    }
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/integrations/cli') {
        return Promise.resolve({ integrations: [card('dingtalk')] })
      }
      if (url === '/api/integrations/cli/dingtalk/login') return Promise.resolve(view)
      return Promise.resolve({})
    })
    mockApi.post.mockImplementation((url: string) => {
      if (url.endsWith('/install')) return Promise.resolve({ ok: true, version: '1.0.69' })
      if (url.endsWith('/login')) return Promise.resolve({ ...view, state: 'starting', url: null, user_code: null })
      return Promise.resolve({})
    })
    mockApi.del.mockResolvedValue({ cancelled: true })

    renderPanel(true)
    fireEvent.click(await screen.findByTestId('cli-connect-dingtalk'))

    const link = await screen.findByTestId('cli-login-url-dingtalk', {}, { timeout: 3000 })
    expect(link.getAttribute('href')).toBe('https://login.example/device')
    expect(screen.getByTestId('cli-login-code-dingtalk').textContent).toBe('ABCD-1234')
    expect(screen.getByText(enTranslation.integrations.cli.hint.dingtalk_auth, { exact: false })).toBeTruthy()

    const posts = mockApi.post.mock.calls.map((c) => [c[0], c[1]])
    expect(posts[0][0]).toBe('/api/integrations/cli/dingtalk/install')
    expect(posts[1]).toEqual(['/api/integrations/cli/dingtalk/login', { enable_after: true }])

    fireEvent.click(screen.getByTestId('cli-cancel-dingtalk'))
    await waitFor(() =>
      expect(mockApi.del).toHaveBeenCalledWith('/api/integrations/cli/dingtalk/login'),
    )
  })

  it('shows the QR image once the server says it is ready', async () => {
    const view = {
      state: 'waiting',
      url: 'https://work.example/scan',
      user_code: null,
      qr: true,
      message: null,
      step: 1,
      steps: 1,
      hint: 'scan_wecom',
    }
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/integrations/cli') {
        return Promise.resolve({
          integrations: [card('wecom', { installed: true, version: '1.3.4', login: { state: 'waiting', step: 1, steps: 1 } })],
        })
      }
      return Promise.resolve(view)
    })
    renderPanel(true)
    // A login started earlier (another tab) is picked up again.
    const img = await screen.findByTestId('cli-login-qr-wecom', {}, { timeout: 3000 })
    expect(img.getAttribute('src')).toMatch(/^\/api\/integrations\/cli\/wecom\/login\/qr\?t=\d+$/)
  })
})

describe('WorkplaceCliPanel cancel', () => {
  it('a poll answered after Cancel does not reopen the panel', async () => {
    const waiting = {
      state: 'waiting', url: 'https://login.example/x', user_code: 'C-1',
      qr: false, message: null, step: 1, steps: 1, hint: 'open_url',
    }
    let releasePoll: (v: unknown) => void = () => {}
    let polls = 0
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/integrations/cli') {
        return Promise.resolve({ integrations: [card('dingtalk', { installed: true, version: '1' })] })
      }
      polls += 1
      if (polls === 1) return Promise.resolve(waiting)
      // The second poll is still in flight when Cancel is clicked.
      return new Promise((r) => { releasePoll = r })
    })
    mockApi.post.mockResolvedValue({ ...waiting, state: 'starting', url: null, user_code: null })
    mockApi.del.mockResolvedValue({ cancelled: true })

    renderPanel(true)
    fireEvent.click(await screen.findByTestId('cli-connect-dingtalk'))
    await screen.findByTestId('cli-login-url-dingtalk', {}, { timeout: 3000 })
    await waitFor(() => expect(polls).toBe(2), { timeout: 4000 })

    fireEvent.click(screen.getByTestId('cli-cancel-dingtalk'))
    await waitFor(() => expect(screen.queryByTestId('cli-login-dingtalk')).toBeNull())
    releasePoll(waiting)  // the stale answer arrives after the cancel
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByTestId('cli-login-dingtalk')).toBeNull()
  })
})

describe('WorkplaceCliPanel outcomes', () => {
  function finished(view: Record<string, unknown>) {
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/integrations/cli') {
        return Promise.resolve({
          integrations: [card(view.key as string, {
            installed: true, version: '1',
            login: { state: 'waiting', step: 1, steps: 1 },
          })],
        })
      }
      return Promise.resolve({
        url: null, user_code: null, qr: false, message: null,
        step: 1, steps: 1, hint: null, ...view,
      })
    })
  }

  it('DingTalk without org CLI access: admins, console and the official guide', async () => {
    finished({
      key: 'dingtalk', state: 'failed',
      message: 'device authorization failed: 您暂无 CLI 数据访问权限，请联系管理员开启',
      notice: 'dingtalk_cli_access', notice_detail: 'Admin A、Admin B',
      help_url: 'https://open-dev.dingtalk.com/fe/old#/developerSettings',
    })
    renderPanel(true)
    const outcome = await screen.findByTestId('cli-outcome-dingtalk', {}, { timeout: 3000 })
    expect(outcome.textContent).toContain('CLI Access Management')
    expect(outcome.textContent).toContain('Admin A、Admin B')
    expect(screen.getByTestId('cli-help-dingtalk').getAttribute('href'))
      .toBe('https://open-dev.dingtalk.com/fe/old#/developerSettings')
    expect(screen.getByTestId('cli-guide-dingtalk').getAttribute('href'))
      .toContain('github.com/DingTalk-Real-AI/dingtalk-workspace-cli')
    // The notice speaks for itself; the raw CLI message is not repeated.
    expect(outcome.textContent).not.toContain('device authorization failed')
  })

  it('Feishu signed in with withheld scopes: a warning, not a failure', async () => {
    finished({
      key: 'feishu', state: 'succeeded',
      notice: 'feishu_missing_scopes', notice_detail: 'vc:meeting.realtime:read',
    })
    renderPanel(true)
    const outcome = await screen.findByTestId('cli-outcome-feishu', {}, { timeout: 3000 })
    expect(outcome.className).toContain('text-amber-700')
    expect(outcome.textContent).toContain('vc:meeting.realtime:read')
    expect(outcome.textContent).not.toContain(enTranslation.integrations.cli.loginFailed)
  })

  it('a failure with no notice shows one readable line', async () => {
    finished({ key: 'wecom', state: 'failed', message: 'QrTimeout: scan timed out' })
    renderPanel(true)
    const outcome = await screen.findByTestId('cli-outcome-wecom', {}, { timeout: 3000 })
    expect(outcome.textContent).toContain(enTranslation.integrations.cli.loginFailed)
    expect(outcome.textContent).toContain('QrTimeout: scan timed out')
  })
})
