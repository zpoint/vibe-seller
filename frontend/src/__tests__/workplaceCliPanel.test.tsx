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

  it('an installed platform has no update button: it updates itself', async () => {
    mockApi.get.mockResolvedValue({
      integrations: [
        card('feishu', {
          installed: true,
          version: '1.0.100',
          enabled: true,
          auth: { usable: true, reason: 'logged_in', identity: 'Alice' },
        }),
        card('wecom', { installed: true, version: '1.3.4' }),
      ],
    })
    renderPanel(true)
    expect(await screen.findByTestId('cli-disable-feishu')).toBeTruthy()
    expect(screen.getByTestId('cli-relogin-feishu')).toBeTruthy()
    expect(screen.getByTestId('cli-connect-wecom')).toBeTruthy()
    const row = screen.getByTestId('cli-row-feishu').textContent ?? ''
    expect(row).not.toContain('Update')
    expect(screen.queryByTestId('cli-update-feishu')).toBeNull()
    expect(screen.queryByTestId('cli-update-wecom')).toBeNull()
    expect(screen.getByTestId('cli-card').textContent).toContain(
      'update to the official latest version every day',
    )
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

describe('WorkplaceCliPanel after the sign-in', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('a stopped platform shows only Connect, even while signed in', async () => {
    mockApi.get.mockResolvedValue({
      integrations: [card('wecom', {
        installed: true, version: '1.3.4', enabled: false,
        auth: { usable: true, reason: 'logged_in', identity: 'Bot' },
      })],
    })
    renderPanel(true)
    expect(await screen.findByTestId('cli-connect-wecom')).toBeTruthy()
    expect(screen.queryByTestId('cli-relogin-wecom')).toBeNull()
    expect(screen.queryByTestId('cli-disable-wecom')).toBeNull()
  })

  it('keeps polling while the server enables, then shows the enabled row', async () => {
    // Signed in, and the server is still fetching the skills: the page
    // used to stop at "signed in, AI can't use it" with Connect shown.
    let polls = 0
    let enabled = false
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/integrations/cli') {
        return Promise.resolve({
          integrations: [card('dingtalk', {
            installed: true, version: '1', enabled,
            auth: { usable: true, reason: 'logged_in', identity: 'Alice' },
            login: { state: 'succeeded', finishing: !enabled, step: 1, steps: 1 },
          })],
        })
      }
      polls += 1
      if (polls > 1) enabled = true
      return Promise.resolve({
        state: 'succeeded', finishing: !enabled, url: null, user_code: null,
        qr: false, message: null, step: 1, steps: 1, hint: null,
      })
    })
    renderPanel(true)
    expect(await screen.findByTestId('cli-finishing-dingtalk', {}, { timeout: 3000 }))
      .toBeTruthy()
    expect(screen.getByTestId('cli-finishing-dingtalk').textContent).toBe(cli.finishing)
    expect(screen.queryByTestId('cli-cancel-dingtalk')).toBeNull()
    expect(screen.queryByTestId('cli-connect-dingtalk')).toBeNull()
    expect(await screen.findByTestId('cli-disable-dingtalk', {}, { timeout: 5000 }))
      .toBeTruthy()
    expect(screen.queryByTestId('cli-finishing-dingtalk')).toBeNull()
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
      notices: [{
        key: 'dingtalk_cli_access', detail: 'Admin A、Admin B',
        url: 'https://open-dev.dingtalk.com/fe/old#/developerSettings',
      }],
    })
    renderPanel(true)
    const outcome = await screen.findByTestId('cli-outcome-dingtalk', {}, { timeout: 3000 })
    // DingTalk's own menu names (renamed since: 「CLI设置」, not 「CLI 访问管理」)
    // and the switch Vibe Seller needs, as steps an admin can follow.
    const steps = screen.getByTestId('cli-notice-steps-dingtalk')
    expect(steps.querySelectorAll('li').length).toBe(cli.noticeSteps.dingtalk_cli_access.length)
    expect(steps.textContent).toContain('CLI设置')
    expect(steps.textContent).toContain('其他渠道')
    expect(outcome.textContent).toContain('Admin A、Admin B')
    expect(screen.getByTestId('cli-help-dingtalk').getAttribute('href'))
      .toBe('https://open-dev.dingtalk.com/fe/old#/developerSettings')
    expect(screen.getByTestId('cli-guide-dingtalk').getAttribute('href'))
      .toBe('https://open.dingtalk.com/document/development/dws-permission-management-guide')
    expect(screen.getByTestId('cli-guide-dingtalk').textContent).toBe(cli.officialGuide)
    // The notice speaks for itself; the raw CLI message is not repeated.
    expect(outcome.textContent).not.toContain('device authorization failed')
  })

  it('Feishu signed in with withheld scopes: a warning, not a failure', async () => {
    finished({
      key: 'feishu', state: 'succeeded',
      notices: [{ key: 'feishu_missing_scopes', detail: 'vc:meeting.realtime:read', url: null }],
    })
    renderPanel(true)
    const outcome = await screen.findByTestId('cli-outcome-feishu', {}, { timeout: 3000 })
    expect(outcome.className).toContain('text-amber-700')
    expect(outcome.textContent).toContain('vc:meeting.realtime:read')
    expect(outcome.textContent).not.toContain(enTranslation.integrations.cli.loginFailed)
  })

  it('Feishu withheld the meeting-screenshot permission: names the feature and links the guide', async () => {
    finished({
      key: 'feishu', state: 'succeeded',
      notices: [{ key: 'feishu_missing_scopes', detail: 'vc:meeting.realtime:read', url: null }],
    })
    renderPanel(true)
    const outcome = await screen.findByTestId('cli-outcome-feishu', {}, { timeout: 3000 })
    // What stops working, in words — and the code an admin searches for.
    expect(outcome.textContent).toContain(cli.scopeFeature.meetingScreenshot)
    expect(outcome.textContent).toContain('vc:meeting.realtime:read')
    const guide = screen.getByTestId('cli-guide-feishu')
    expect(guide.getAttribute('href'))
      .toBe('https://docs.vibe-sellers.com/en/workplace-cli-feishu/#admin-restricted-permissions')
    expect(guide.textContent).toBe(cli.guideLink.feishu_missing_scopes)
  })

  it('Feishu withheld a permission the page does not know: its code as is', async () => {
    finished({
      key: 'feishu', state: 'succeeded',
      notices: [{
        key: 'feishu_missing_scopes',
        detail: 'vc:meeting.realtime:read im:example.scope:read',
        url: null,
      }],
    })
    renderPanel(true)
    const outcome = await screen.findByTestId('cli-outcome-feishu', {}, { timeout: 3000 })
    expect(outcome.textContent).toContain('im:example.scope:read')
    expect(outcome.textContent).toContain(cli.scopeFeature.meetingScreenshot)
  })

  it('Feishu: withheld scopes AND a sign-in that cannot renew, both shown', async () => {
    finished({
      key: 'feishu', state: 'succeeded',
      notices: [
        { key: 'feishu_missing_scopes', detail: 'vc:meeting.realtime:read', url: null },
        {
          key: 'feishu_no_refresh_token', detail: '2026-11-20T14:05:00+08:00',
          url: 'https://open.feishu.cn/app/cli_x/safe',
        },
      ],
    })
    renderPanel(true)
    const outcome = await screen.findByTestId('cli-outcome-feishu', {}, { timeout: 3000 })
    expect(outcome.className).toContain('text-amber-700')  // signed in: a warning
    expect(outcome.textContent).toContain('vc:meeting.realtime:read')
    const expiry = screen.getByTestId('cli-notice-feishu-feishu_no_refresh_token')
    // The moment, in the reader's own time zone — not the raw ISO text.
    expect(expiry.textContent).toContain(
      new Date('2026-11-20T14:05:00+08:00').toLocaleString('en'),
    )
    expect(expiry.textContent).not.toContain('2026-11-20T14:05')
    const link = screen.getByTestId('cli-help-feishu')
    expect(link.getAttribute('href')).toBe('https://open.feishu.cn/app/cli_x/safe')
    expect(link.textContent).toBe(cli.helpLink.feishu_no_refresh_token)
  })

  it('a failure with no notice shows one readable line', async () => {
    finished({ key: 'wecom', state: 'failed', message: 'QrTimeout: scan timed out' })
    renderPanel(true)
    const outcome = await screen.findByTestId('cli-outcome-wecom', {}, { timeout: 3000 })
    expect(outcome.textContent).toContain(
      enTranslation.integrations.cli.loginFailed.replace('{{button}}', cli.connect),
    )
    expect(outcome.textContent).toContain('QrTimeout: scan timed out')
  })

  it('a sign-in that timed out says so, and which button starts over', async () => {
    finished({
      key: 'feishu', state: 'failed', error: 'timeout',
      message: 'timed out waiting for the approval',
    })
    renderPanel(true)
    const outcome = await screen.findByTestId('cli-outcome-feishu', {}, { timeout: 3000 })
    expect(outcome.textContent).toContain(
      cli.loginError.timeout.replace('{{button}}', cli.connect),
    )
    // The technical line is kept, folded under Details.
    expect(outcome.querySelector('details')?.textContent).toContain('timed out waiting')
  })

  it('signed in but not enabled: red, and Connect retries the enabling', async () => {
    finished({
      key: 'feishu', state: 'succeeded', error: 'enable_failed',
      message: 'signed in, but enabling failed: boom',
    })
    renderPanel(true)
    const outcome = await screen.findByTestId('cli-outcome-feishu', {}, { timeout: 3000 })
    expect(outcome.className).toContain('text-red-600')
    expect(outcome.textContent).toContain(
      cli.loginError.enable_failed.replace('{{button}}', cli.connect),
    )
  })

  it('the DingTalk notice names the button that is on screen', async () => {
    // Never signed in → the row shows Connect, not Sign in again.
    finished({
      key: 'dingtalk', state: 'failed',
      notices: [{ key: 'dingtalk_cli_access', detail: 'Admin A', url: null }],
    })
    renderPanel(true)
    const outcome = await screen.findByTestId('cli-outcome-dingtalk', {}, { timeout: 3000 })
    expect(screen.getByTestId('cli-connect-dingtalk')).toBeTruthy()
    expect(screen.queryByTestId('cli-relogin-dingtalk')).toBeNull()
    expect(outcome.textContent).toContain(`click ${cli.connect}`)
    expect(outcome.textContent).not.toContain(cli.relogin)
  })
})

describe('WorkplaceCliPanel errors', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('a failed download says what to do next and keeps the detail folded', async () => {
    mockApi.get.mockResolvedValue({ integrations: [card('wecom')] })
    mockApi.post.mockRejectedValue(Object.assign(new Error('x'), {
      detail: { code: 'download_failed', message: 'WeCom install: https://registry.npmjs.org/… timed out' },
    }))
    renderPanel(true)
    fireEvent.click(await screen.findByTestId('cli-connect-wecom'))
    const err = await screen.findByTestId('cli-error')
    expect(err.textContent).toContain(cli.error.download_failed)
    expect(err.querySelector('details')?.textContent).toContain('registry.npmjs.org')
  })

  it('an error the page has no sentence for still says what to do', async () => {
    mockApi.get.mockResolvedValue({ integrations: [card('wecom')] })
    mockApi.post.mockRejectedValue(new Error('Request failed with status 502'))
    renderPanel(true)
    fireEvent.click(await screen.findByTestId('cli-connect-wecom'))
    const err = await screen.findByTestId('cli-error')
    expect(err.textContent).toContain(cli.error.unexpected)
    expect(err.textContent).toContain('status 502')
  })

  it('platform names come from the translations, not the server', async () => {
    mockApi.get.mockResolvedValue({ integrations: [card('wecom')] })
    renderPanel(false)
    const row = await screen.findByTestId('cli-row-wecom')
    expect(row.textContent).toContain(cli.platform.wecom)
    expect(row.textContent).not.toContain('WECOM')
  })
})
