/**
 * Render tests for ChatBotSection (multi-platform chat bots).
 *
 * Mocks the `api` helper so the component's list/create/update/
 * delete/test flows can be exercised end-to-end in jsdom. Beyond the
 * original WeCom CRUD coverage this pins the multi-platform contract:
 * the live platform badge from /platforms metadata, the
 * signing-secret field appearing ONLY for DingTalk/Feishu, and the
 * "signed" indicator on list rows.
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

import { ChatBotSection } from '../components/ChatBotSection'

const i18nTestInstance = i18n.createInstance()
i18nTestInstance.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

function renderSection() {
  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <ChatBotSection />
    </I18nextProvider>,
  )
}

const PLATFORMS = [
  {
    name: 'wecom',
    label: 'WeCom',
    hosts: ['qyapi.weixin.qq.com'],
    path_prefix: '/cgi-bin/webhook/',
    uses_secret: false,
    supports_file: true,
    max_message: 4096,
    limits: {},
    limit_unit: 'bytes',
    doc_url: 'https://developer.work.weixin.qq.com/document/path/91770',
  },
  {
    name: 'dingtalk',
    label: 'DingTalk',
    hosts: ['oapi.dingtalk.com'],
    path_prefix: '/robot/send',
    uses_secret: true,
    supports_file: false,
    max_message: 20000,
    limits: {},
    limit_unit: 'bytes',
    doc_url: 'https://open.dingtalk.com/document/orgapp/custom-robot-access',
  },
  {
    name: 'feishu',
    label: 'Feishu / Lark',
    hosts: ['open.feishu.cn', 'open.larksuite.com'],
    path_prefix: '/open-apis/bot/v2/hook/',
    uses_secret: true,
    supports_file: false,
    max_message: 20000,
    limits: {},
    limit_unit: 'bytes',
    doc_url: 'https://open.feishu.cn/document/client-docs/bot-v2/add-custom-bot',
  },
  {
    name: 'slack',
    label: 'Slack',
    hosts: ['hooks.slack.com', 'hooks.slack-gov.com'],
    path_prefix: '/services/',
    uses_secret: false,
    supports_file: false,
    max_message: 40000,
    limits: { markdown: 12000 },
    limit_unit: 'chars',
    doc_url: 'https://api.slack.com/messaging/webhooks',
  },
]

const WECOM_URL = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=real-abcd'
const DINGTALK_URL = 'https://oapi.dingtalk.com/robot/send?access_token=tok-abcd'
const SLACK_URL = 'https://hooks.slack.com/services/T000/B000/xxxxxxxxxxxx'

const BOT_A_SUMMARY = {
  id: 'bot-a',
  name: 'Ops Alerts',
  platform: 'wecom',
  webhook_url_masked: 'https://qyapi.weixin.qq.com/...****abcd',
  has_secret: false,
  can_send_file: true,
  created_at: '2026-04-17T10:00:00Z',
  updated_at: '2026-04-17T10:00:00Z',
}

const BOT_DING_SUMMARY = {
  id: 'bot-ding',
  name: 'Ops DingTalk',
  platform: 'dingtalk',
  webhook_url_masked: 'https://oapi.dingtalk.com/...****abcd',
  has_secret: true,
  can_send_file: false,
  created_at: '2026-04-17T10:00:00Z',
  updated_at: '2026-04-17T10:00:00Z',
}

const BOT_A_FULL = {
  id: 'bot-a',
  name: 'Ops Alerts',
  platform: 'wecom',
  webhook_url: WECOM_URL,
  secret: null,
  created_at: '2026-04-17T10:00:00Z',
  updated_at: '2026-04-17T10:00:00Z',
}

describe('ChatBotSection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockReset()
    mockApi.post.mockReset()
    mockApi.put.mockReset()
    mockApi.del.mockReset()
    // Default happy path: empty bot list + platform registry.
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/chat-bots/platforms') return Promise.resolve(PLATFORMS)
      return Promise.resolve([])
    })
    // Silence window.confirm
    window.confirm = vi.fn(() => true)
  })

  it('renders empty state after list returns []', async () => {
    renderSection()

    expect(mockApi.get).toHaveBeenCalledWith('/api/chat-bots')
    expect(await screen.findByTestId('chat-bot-empty')).toBeInTheDocument()
    expect(screen.queryByTestId('chat-bot-list')).not.toBeInTheDocument()
  })

  it('renders list rows with platform badge and masked URL', async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/chat-bots/platforms') return Promise.resolve(PLATFORMS)
      return Promise.resolve([BOT_A_SUMMARY])
    })
    renderSection()

    const row = await screen.findByTestId('chat-bot-row-bot-a')
    expect(row).toHaveTextContent('Ops Alerts')
    expect(row).toHaveTextContent('WeCom')
    expect(row).toHaveTextContent('qyapi.weixin.qq.com')
    // Masked form only — raw secret key must not appear
    expect(row).toHaveTextContent('****abcd')
    expect(row.textContent).not.toMatch(/real-abcd/)
    // WeCom bot is not signed → no signed pill
    expect(
      screen.queryByTestId('chat-bot-signed-bot-a'),
    ).not.toBeInTheDocument()
  })

  it('shows a signed indicator for a bot with a secret', async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/chat-bots/platforms') return Promise.resolve(PLATFORMS)
      return Promise.resolve([BOT_DING_SUMMARY])
    })
    renderSection()

    const row = await screen.findByTestId('chat-bot-row-bot-ding')
    expect(row).toHaveTextContent('DingTalk')
    expect(
      screen.getByTestId('chat-bot-signed-bot-ding'),
    ).toHaveTextContent('signed')
  })

  // ── live platform badge + secret field ───────────────

  it('detects the platform from the URL while typing and shows the badge', async () => {
    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-add'))

    const url = screen.getByTestId('chat-bot-url') as HTMLInputElement
    fireEvent.change(url, { target: { value: DINGTALK_URL } })

    expect(
      await screen.findByTestId('chat-bot-platform-badge'),
    ).toHaveTextContent('DingTalk')
    // DingTalk uses a signing secret → the field appears.
    expect(screen.getByTestId('chat-bot-secret')).toBeInTheDocument()
    // Per-platform hint replaces the generic one.
    expect(screen.getByTestId('chat-bot-url-hint').textContent).toMatch(
      /自定义机器人|Add robot/,
    )
  })

  it.each([
    [WECOM_URL, 'WeCom', false],
    [DINGTALK_URL, 'DingTalk', true],
    ['https://open.feishu.cn/open-apis/bot/v2/hook/tok-1234', 'Feishu / Lark', true],
    [SLACK_URL, 'Slack', false],
  ])('%s → badge %s, secret field %s', async (url, label, hasSecret) => {
    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-add'))

    fireEvent.change(screen.getByTestId('chat-bot-url'), {
      target: { value: url },
    })

    expect(
      await screen.findByTestId('chat-bot-platform-badge'),
    ).toHaveTextContent(label)
    expect(!!screen.queryByTestId('chat-bot-secret')).toBe(hasSecret)
  })

  it('shows the generic hint while the URL is unrecognized', async () => {
    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-add'))

    fireEvent.change(screen.getByTestId('chat-bot-url'), {
      target: { value: 'https://evil.com/webhook' },
    })

    // No badge text (still the detecting placeholder), no secret.
    expect(
      screen.getByTestId('chat-bot-platform-badge'),
    ).toHaveTextContent(/detected/i)
    expect(screen.queryByTestId('chat-bot-secret')).not.toBeInTheDocument()
  })

  it('saves a signed DingTalk bot with its secret', async () => {
    mockApi.post.mockResolvedValueOnce({ ...BOT_DING_SUMMARY })
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/chat-bots/platforms') return Promise.resolve(PLATFORMS)
      return Promise.resolve([])
    })
    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-add'))

    fireEvent.change(screen.getByTestId('chat-bot-name'), {
      target: { value: 'Ops DingTalk' },
    })
    fireEvent.change(screen.getByTestId('chat-bot-url'), {
      target: { value: DINGTALK_URL },
    })
    fireEvent.change(await screen.findByTestId('chat-bot-secret'), {
      target: { value: 'SECtest' },
    })
    fireEvent.click(screen.getByTestId('chat-bot-save'))

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/api/chat-bots', {
        name: 'Ops DingTalk',
        webhook_url: DINGTALK_URL,
        secret: 'SECtest',
      })
    })
  })

  it('saves a WeCom bot without any secret field being sent as non-empty', async () => {
    mockApi.post.mockResolvedValueOnce(BOT_A_FULL)
    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-add'))

    fireEvent.change(screen.getByTestId('chat-bot-name'), {
      target: { value: 'Ops Alerts' },
    })
    fireEvent.change(screen.getByTestId('chat-bot-url'), {
      target: { value: WECOM_URL },
    })
    fireEvent.click(screen.getByTestId('chat-bot-save'))

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/api/chat-bots', {
        name: 'Ops Alerts',
        webhook_url: WECOM_URL,
        secret: '',
      })
    })
    expect(await screen.findByTestId('chat-bot-empty')).toBeInTheDocument()
    expect(screen.queryByTestId('chat-bot-form')).not.toBeInTheDocument()
  })

  it('save is blocked when name or url blank (no API call)', async () => {
    renderSection()
    await screen.findByTestId('chat-bot-empty')

    fireEvent.click(screen.getByTestId('chat-bot-add'))
    fireEvent.click(screen.getByTestId('chat-bot-save'))

    const toast = await screen.findByTestId('chat-bot-toast')
    expect(toast.className).toContain('text-red-600')
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('Edit fetches full URL + secret via single GET and pre-fills the form', async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/chat-bots/platforms') return Promise.resolve(PLATFORMS)
      if (url === '/api/chat-bots/bot-a') return Promise.resolve(BOT_A_FULL)
      return Promise.resolve([BOT_A_SUMMARY])
    })
    mockApi.put.mockResolvedValueOnce({ ...BOT_A_FULL, name: 'Renamed' })
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/chat-bots/platforms') return Promise.resolve(PLATFORMS)
      if (url === '/api/chat-bots/bot-a') return Promise.resolve(BOT_A_FULL)
      return Promise.resolve([{ ...BOT_A_SUMMARY, name: 'Renamed' }])
    })

    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-edit-bot-a'))

    // After the single GET resolves, the full URL shows up in the
    // edit form (not the masked one).
    await waitFor(() => {
      const urlInput = screen.getByTestId('chat-bot-url') as HTMLInputElement
      expect(urlInput.value).toBe(WECOM_URL)
    })
    expect(mockApi.get).toHaveBeenCalledWith('/api/chat-bots/bot-a')
    // WeCom bot → no secret field even in edit mode.
    expect(screen.queryByTestId('chat-bot-secret')).not.toBeInTheDocument()

    fireEvent.change(screen.getByTestId('chat-bot-name'), {
      target: { value: 'Renamed' },
    })
    fireEvent.click(screen.getByTestId('chat-bot-save'))

    await waitFor(() => {
      expect(mockApi.put).toHaveBeenCalledWith('/api/chat-bots/bot-a', {
        name: 'Renamed',
        webhook_url: WECOM_URL,
        secret: '',
      })
    })
    const row = await screen.findByTestId('chat-bot-row-bot-a')
    expect(row).toHaveTextContent('Renamed')
  })

  it('Delete calls DEL after confirm and refreshes', async () => {
    let deleted = false
    mockApi.del.mockImplementation(async () => {
      deleted = true
      return { ok: true }
    })
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/chat-bots/platforms') return Promise.resolve(PLATFORMS)
      return Promise.resolve(deleted ? [] : [BOT_A_SUMMARY])
    })

    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-delete-bot-a'))

    await waitFor(() => {
      expect(mockApi.del).toHaveBeenCalledWith('/api/chat-bots/bot-a')
    })
    await screen.findByTestId('chat-bot-empty')
    expect(screen.queryByTestId('chat-bot-row-bot-a')).not.toBeInTheDocument()
  })

  it('Delete aborts if user cancels the confirm dialog', async () => {
    window.confirm = vi.fn(() => false)
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/chat-bots/platforms') return Promise.resolve(PLATFORMS)
      return Promise.resolve([BOT_A_SUMMARY])
    })

    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-delete-bot-a'))

    expect(mockApi.del).not.toHaveBeenCalled()
  })

  it('Test button posts to /test and shows success toast on ok=true', async () => {
    mockApi.post.mockResolvedValueOnce({ ok: true, message: 'Message sent' })
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/chat-bots/platforms') return Promise.resolve(PLATFORMS)
      return Promise.resolve([BOT_A_SUMMARY])
    })

    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-test-bot-a'))

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        '/api/chat-bots/bot-a/test',
        {},
      )
    })
    const toast = await screen.findByTestId('chat-bot-toast')
    expect(toast.className).toContain('text-green-700')
  })

  it('Test button shows error toast on ok=false with server message', async () => {
    mockApi.post.mockResolvedValueOnce({
      ok: false,
      message: 'keyword not in content',
    })
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/chat-bots/platforms') return Promise.resolve(PLATFORMS)
      return Promise.resolve([BOT_A_SUMMARY])
    })

    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-test-bot-a'))

    const toast = await screen.findByTestId('chat-bot-toast')
    expect(toast.className).toContain('text-red-600')
    expect(toast.textContent).toMatch(/keyword not in content/)
  })

  it('surfaces list-fetch error in the error banner', async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/chat-bots/platforms') return Promise.resolve(PLATFORMS)
      return Promise.reject(new Error('boom'))
    })
    renderSection()

    const err = await screen.findByTestId('chat-bot-error')
    expect(err.textContent).toContain('boom')
  })
})
