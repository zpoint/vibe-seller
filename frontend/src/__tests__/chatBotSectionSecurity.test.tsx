/**
 * Render tests for ChatBotSection's per-platform SECURITY section.
 *
 * The section renders ONLY from the registry's `security_options` /
 * `security_required` — DingTalk: at least one of its settings is
 * required; Feishu: all optional; WeCom/Slack: no section at all.
 * Fields reveal per chosen setting, every hint is platform-specific,
 * refusals are inline, and the save payload carries the chosen
 * `security_mode` so switching a setting off really turns it off.
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
    uses_keyword: false,
    security_options: [],
    security_required: false,
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
    uses_keyword: true,
    security_options: ['sign', 'keyword', 'ip'],
    security_required: true,
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
    uses_keyword: true,
    security_options: ['sign', 'keyword', 'ip'],
    security_required: false,
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
    uses_keyword: false,
    security_options: [],
    security_required: false,
    supports_file: false,
    max_message: 40000,
    limits: { markdown: 12000 },
    limit_unit: 'chars',
    doc_url: 'https://api.slack.com/messaging/webhooks',
  },
]

const WECOM_URL = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=real-abcd'
const DINGTALK_URL = 'https://oapi.dingtalk.com/robot/send?access_token=tok-abcd'
const FEISHU_URL = 'https://open.feishu.cn/open-apis/bot/v2/hook/tok-1234'
const SLACK_URL = 'https://hooks.slack.com/services/T000/B000/xxxxxxxxxxxx'

const BOT_DING_SUMMARY = {
  id: 'bot-ding',
  name: 'Ops DingTalk',
  platform: 'dingtalk',
  webhook_url_masked: 'https://oapi.dingtalk.com/...****abcd',
  has_secret: true,
  keyword: 'SALES',
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
  keyword: null,
  security_mode: null,
  created_at: '2026-04-17T10:00:00Z',
  updated_at: '2026-04-17T10:00:00Z',
}

const BOT_DING_FULL_MASKED = {
  id: 'bot-ding',
  name: 'Ops DingTalk',
  platform: 'dingtalk',
  // the server returns masks, never the stored values
  webhook_url: '****',
  secret: '****abcd',
  keyword: 'SALES' as string | null,
  // what the server derives for this legacy row (secret + keyword
  // filled → both settings in play)
  security_mode: 'sign,keyword' as string | null,
  created_at: '2026-04-17T10:00:00Z',
  updated_at: '2026-04-17T10:00:00Z',
}

/** Open the create form and paste a webhook URL. */
async function openFormWithUrl(url: string) {
  fireEvent.click(await screen.findByTestId('chat-bot-add'))
  fireEvent.change(screen.getByTestId('chat-bot-url'), {
    target: { value: url },
  })
  await screen.findByTestId('chat-bot-platform-badge')
}

/** Tick (or untick) one of the platform's security settings. */
function chooseOption(option: string) {
  fireEvent.click(screen.getByTestId(`chat-bot-mode-${option}`))
}

function mockMaskedEdit(full: typeof BOT_DING_FULL_MASKED = BOT_DING_FULL_MASKED) {
  mockApi.get.mockImplementation((url: string) => {
    if (url === '/api/chat-bots/platforms') return Promise.resolve(PLATFORMS)
    if (url === '/api/chat-bots/bot-ding') return Promise.resolve(full)
    return Promise.resolve([BOT_DING_SUMMARY])
  })
  mockApi.put.mockResolvedValueOnce(full)
}

describe('ChatBotSection security settings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockReset()
    mockApi.post.mockReset()
    mockApi.put.mockReset()
    mockApi.del.mockReset()
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/chat-bots/platforms') return Promise.resolve(PLATFORMS)
      return Promise.resolve([])
    })
    window.confirm = vi.fn(() => true)
  })

  // ── what each platform shows ──────────────────────

  it('shows nothing but name + URL while no platform is detected', async () => {
    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-add'))

    fireEvent.change(screen.getByTestId('chat-bot-url'), {
      target: { value: 'https://evil.com/webhook' },
    })

    // No badge text (still the detecting placeholder), no security
    // section at all.
    expect(
      screen.getByTestId('chat-bot-platform-badge'),
    ).toHaveTextContent(/detected/i)
    expect(
      screen.queryByTestId('chat-bot-security'),
    ).not.toBeInTheDocument()
    expect(screen.queryByTestId('chat-bot-secret')).not.toBeInTheDocument()
    expect(screen.queryByTestId('chat-bot-keyword')).not.toBeInTheDocument()
  })

  it.each([[WECOM_URL], [SLACK_URL]])(
    'shows no security section for %s (the URL is the credential)',
    async (url) => {
      renderSection()
      await openFormWithUrl(url)

      expect(
        screen.queryByTestId('chat-bot-security'),
      ).not.toBeInTheDocument()
      expect(screen.queryByTestId('chat-bot-mode-sign')).not.toBeInTheDocument()
      expect(screen.queryByTestId('chat-bot-secret')).not.toBeInTheDocument()
      expect(screen.queryByTestId('chat-bot-keyword')).not.toBeInTheDocument()
    },
  )

  it.each([
    [DINGTALK_URL, /pick at least one/],
    [FEISHU_URL, /optional/],
  ])('%s → security section titled %s', async (url, title) => {
    renderSection()
    await openFormWithUrl(url)

    const section = screen.getByTestId('chat-bot-security')
    expect(section.textContent).toMatch(title)
  })

  it('DingTalk reveals only the ticked setting\'s field — several can combine', async () => {
    renderSection()
    await openFormWithUrl(DINGTALK_URL)

    const sign = screen.getByTestId('chat-bot-mode-sign') as HTMLInputElement
    const keyword = screen.getByTestId('chat-bot-mode-keyword') as HTMLInputElement
    const ip = screen.getByTestId('chat-bot-mode-ip') as HTMLInputElement
    expect(sign.type).toBe('checkbox')
    expect(sign.checked).toBe(false)

    fireEvent.click(sign)
    expect(sign.checked).toBe(true)
    expect(screen.getByTestId('chat-bot-secret')).toBeInTheDocument()
    // Only the CHOSEN option's field shows.
    expect(screen.queryByTestId('chat-bot-keyword')).not.toBeInTheDocument()
    expect(screen.queryByTestId('chat-bot-ip-note')).not.toBeInTheDocument()

    // A robot can enforce 加签 AND 自定义关键词 at once — the form
    // records both, each with its own field.
    fireEvent.click(keyword)
    expect(sign.checked).toBe(true)
    expect(keyword.checked).toBe(true)
    expect(screen.getByTestId('chat-bot-secret')).toBeInTheDocument()
    expect(screen.getByTestId('chat-bot-keyword')).toBeInTheDocument()

    fireEvent.click(sign)
    expect(sign.checked).toBe(false)
    expect(screen.queryByTestId('chat-bot-secret')).not.toBeInTheDocument()
    expect(screen.getByTestId('chat-bot-keyword')).toBeInTheDocument()

    fireEvent.click(ip)
    // IP needs no field — the note says the range lives platform-side.
    expect(screen.queryByTestId('chat-bot-secret')).not.toBeInTheDocument()
    expect(screen.getByTestId('chat-bot-keyword')).toBeInTheDocument()
    expect(screen.getByTestId('chat-bot-ip-note').textContent).toMatch(
      /nothing to fill in here/,
    )
  })

  it('Feishu is optional — ticked settings reveal their fields, several at once', async () => {
    renderSection()
    await openFormWithUrl(FEISHU_URL)

    const sign = screen.getByTestId('chat-bot-mode-sign') as HTMLInputElement
    const keyword = screen.getByTestId('chat-bot-mode-keyword') as HTMLInputElement
    expect(sign.type).toBe('checkbox')
    // Optional: nothing ticked, no fields, no error.
    expect(screen.queryByTestId('chat-bot-secret')).not.toBeInTheDocument()
    expect(screen.queryByTestId('chat-bot-keyword')).not.toBeInTheDocument()

    fireEvent.click(sign)
    expect(screen.getByTestId('chat-bot-secret')).toBeInTheDocument()
    expect(screen.queryByTestId('chat-bot-keyword')).not.toBeInTheDocument()

    fireEvent.click(keyword)
    expect(sign.checked).toBe(true)
    expect(keyword.checked).toBe(true)
    expect(screen.getByTestId('chat-bot-secret')).toBeInTheDocument()
    expect(screen.getByTestId('chat-bot-keyword')).toBeInTheDocument()

    fireEvent.click(sign)
    expect(sign.checked).toBe(false)
    expect(screen.queryByTestId('chat-bot-secret')).not.toBeInTheDocument()
    expect(screen.getByTestId('chat-bot-keyword')).toBeInTheDocument()
  })

  it('hints are platform-specific — no shared "DingTalk and Feishu" hint', async () => {
    renderSection()
    await openFormWithUrl(DINGTALK_URL)
    chooseOption('sign')

    const hint = screen.getByTestId('chat-bot-secret-hint').textContent ?? ''
    expect(hint).toMatch(/DingTalk|钉钉|this robot/)
    expect(hint).not.toMatch(/Feishu|飞书/)

    fireEvent.click(screen.getByTestId('chat-bot-mode-keyword'))
    const keywordHint =
      screen.getByTestId('chat-bot-keyword-hint').textContent ?? ''
    expect(keywordHint).not.toMatch(/同理|the same way/)
    expect(keywordHint).not.toMatch(/Feishu|飞书/)
  })

  // ── required choice + inline errors ───────────────

  it('DingTalk save without a chosen setting shows an inline error and posts nothing', async () => {
    renderSection()
    await openFormWithUrl(DINGTALK_URL)
    fireEvent.change(screen.getByTestId('chat-bot-name'), {
      target: { value: 'Ops DingTalk' },
    })
    fireEvent.click(screen.getByTestId('chat-bot-save'))

    const err = await screen.findByTestId('chat-bot-security-error')
    expect(err.textContent).toMatch(/pick the setting you configured there/)
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('a chosen setting without its value is refused inline', async () => {
    renderSection()
    await openFormWithUrl(DINGTALK_URL)
    fireEvent.change(screen.getByTestId('chat-bot-name'), {
      target: { value: 'Ops DingTalk' },
    })

    chooseOption('sign')
    fireEvent.click(screen.getByTestId('chat-bot-save'))
    expect(
      (await screen.findByTestId('chat-bot-security-error')).textContent,
    ).toMatch(/needs its key/)
    expect(mockApi.post).not.toHaveBeenCalled()

    chooseOption('sign') // untick
    chooseOption('keyword')
    fireEvent.click(screen.getByTestId('chat-bot-save'))
    expect(
      screen.getByTestId('chat-bot-security-error').textContent,
    ).toMatch(/needs a keyword/)
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  // ── save payloads ─────────────────────────────────

  it('saves a DingTalk bot with its keyword', async () => {
    mockApi.post.mockResolvedValueOnce({ ...BOT_DING_SUMMARY })
    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-add'))

    fireEvent.change(screen.getByTestId('chat-bot-name'), {
      target: { value: 'Ops DingTalk' },
    })
    fireEvent.change(screen.getByTestId('chat-bot-url'), {
      target: { value: DINGTALK_URL },
    })
    chooseOption('keyword')
    fireEvent.change(await screen.findByTestId('chat-bot-keyword'), {
      target: { value: 'SALES' },
    })
    fireEvent.click(screen.getByTestId('chat-bot-save'))

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/api/chat-bots', {
        name: 'Ops DingTalk',
        webhook_url: DINGTALK_URL,
        secret: '',
        keyword: 'SALES',
        security_mode: 'keyword',
      })
    })
  })

  it('saves a signed DingTalk bot with its secret', async () => {
    mockApi.post.mockResolvedValueOnce({ ...BOT_DING_SUMMARY })
    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-add'))

    fireEvent.change(screen.getByTestId('chat-bot-name'), {
      target: { value: 'Ops DingTalk' },
    })
    fireEvent.change(screen.getByTestId('chat-bot-url'), {
      target: { value: DINGTALK_URL },
    })
    chooseOption('sign')
    fireEvent.change(await screen.findByTestId('chat-bot-secret'), {
      target: { value: 'SECtest' },
    })
    fireEvent.click(screen.getByTestId('chat-bot-save'))

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/api/chat-bots', {
        name: 'Ops DingTalk',
        webhook_url: DINGTALK_URL,
        secret: 'SECtest',
        keyword: '',
        security_mode: 'sign',
      })
    })
  })

  it('saves a DingTalk bot with several settings at once', async () => {
    mockApi.post.mockResolvedValueOnce({ ...BOT_DING_SUMMARY })
    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-add'))

    fireEvent.change(screen.getByTestId('chat-bot-name'), {
      target: { value: 'Ops DingTalk' },
    })
    fireEvent.change(screen.getByTestId('chat-bot-url'), {
      target: { value: DINGTALK_URL },
    })
    chooseOption('sign')
    chooseOption('keyword')
    fireEvent.change(await screen.findByTestId('chat-bot-secret'), {
      target: { value: 'SECtest' },
    })
    fireEvent.change(screen.getByTestId('chat-bot-keyword'), {
      target: { value: 'SALES' },
    })
    fireEvent.click(screen.getByTestId('chat-bot-save'))

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/api/chat-bots', {
        name: 'Ops DingTalk',
        webhook_url: DINGTALK_URL,
        secret: 'SECtest',
        keyword: 'SALES',
        security_mode: 'sign,keyword',
      })
    })
  })

  it('saves an IP-whitelisted DingTalk bot with both fields empty on purpose', async () => {
    mockApi.post.mockResolvedValueOnce({ ...BOT_DING_SUMMARY })
    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-add'))

    fireEvent.change(screen.getByTestId('chat-bot-name'), {
      target: { value: 'Ops DingTalk' },
    })
    fireEvent.change(screen.getByTestId('chat-bot-url'), {
      target: { value: DINGTALK_URL },
    })
    chooseOption('ip')
    fireEvent.click(screen.getByTestId('chat-bot-save'))

    // The mode is what says "IP on purpose" vs "forgot to fill".
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/api/chat-bots', {
        name: 'Ops DingTalk',
        webhook_url: DINGTALK_URL,
        secret: '',
        keyword: '',
        security_mode: 'ip',
      })
    })
  })

  it('saves a Feishu bot with no security setting at all', async () => {
    mockApi.post.mockResolvedValueOnce({ ...BOT_A_FULL, platform: 'feishu' })
    renderSection()
    await openFormWithUrl(FEISHU_URL)

    fireEvent.change(screen.getByTestId('chat-bot-name'), {
      target: { value: 'Ops Feishu' },
    })
    fireEvent.click(screen.getByTestId('chat-bot-save'))

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/api/chat-bots', {
        name: 'Ops Feishu',
        webhook_url: FEISHU_URL,
        secret: '',
        keyword: '',
        security_mode: '',
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
        keyword: '',
        security_mode: '',
      })
    })
    expect(await screen.findByTestId('chat-bot-empty')).toBeInTheDocument()
    expect(screen.queryByTestId('chat-bot-form')).not.toBeInTheDocument()
  })

  // ── masked edit form (stored secrets are masks for every caller) ──

  it('edit with the masked URL still shows the stored platform fields', async () => {
    mockMaskedEdit()
    renderSection()

    fireEvent.click(await screen.findByTestId('chat-bot-edit-bot-ding'))
    await waitFor(() => {
      expect(
        (screen.getByTestId('chat-bot-url') as HTMLInputElement).value,
      ).toBe('****')
    })
    // the mask parses as no platform — the form falls back to the
    // bot's STORED platform (dingtalk) for badge + security section
    expect(screen.getByTestId('chat-bot-platform-badge')).toHaveTextContent(
      'DingTalk',
    )
    // the edit form opens on the stored MODE: both settings in play
    expect(
      (screen.getByTestId('chat-bot-mode-sign') as HTMLInputElement).checked,
    ).toBe(true)
    expect(
      (screen.getByTestId('chat-bot-mode-keyword') as HTMLInputElement).checked,
    ).toBe(true)
    expect(screen.getByTestId('chat-bot-secret')).toBeInTheDocument()
    expect(screen.getByTestId('chat-bot-keyword')).toBeInTheDocument()

    // saving the untouched form posts the masks back — the server
    // restores them to the stored values ("unchanged")
    fireEvent.click(screen.getByTestId('chat-bot-save'))
    await waitFor(() => {
      expect(mockApi.put).toHaveBeenCalledWith('/api/chat-bots/bot-ding', {
        name: 'Ops DingTalk',
        webhook_url: '****',
        secret: '****abcd',
        keyword: 'SALES',
        security_mode: 'sign,keyword',
      })
    })
  })

  it('the edit form opens on the stored single mode (sign)', async () => {
    mockMaskedEdit({
      ...BOT_DING_FULL_MASKED,
      keyword: null,
      security_mode: 'sign',
    })
    renderSection()

    fireEvent.click(await screen.findByTestId('chat-bot-edit-bot-ding'))
    await waitFor(() => {
      expect(
        (screen.getByTestId('chat-bot-mode-sign') as HTMLInputElement).checked,
      ).toBe(true)
    })
    expect(screen.getByTestId('chat-bot-secret')).toBeInTheDocument()
    expect(screen.queryByTestId('chat-bot-keyword')).not.toBeInTheDocument()
  })

  it('switching off 加签 on edit clears the secret', async () => {
    mockMaskedEdit()
    renderSection()

    fireEvent.click(await screen.findByTestId('chat-bot-edit-bot-ding'))
    await waitFor(() => {
      expect(
        (screen.getByTestId('chat-bot-mode-sign') as HTMLInputElement).checked,
      ).toBe(true)
    })

    fireEvent.click(screen.getByTestId('chat-bot-mode-sign'))
    fireEvent.click(screen.getByTestId('chat-bot-save'))

    await waitFor(() => {
      expect(mockApi.put).toHaveBeenCalledWith('/api/chat-bots/bot-ding', {
        name: 'Ops DingTalk',
        webhook_url: '****',
        // '' is how the form says "no longer used" — the server drops
        // the stored secret instead of keeping one nothing signs with
        secret: '',
        keyword: 'SALES',
        security_mode: 'keyword',
      })
    })
  })

  it('admins see Show/Copy reveal buttons on the edit form', async () => {
    mockMaskedEdit()
    render(
      <I18nextProvider i18n={i18nTestInstance}>
        <ChatBotSection isAdmin />
      </I18nextProvider>,
    )
    fireEvent.click(await screen.findByTestId('chat-bot-edit-bot-ding'))
    await waitFor(() => {
      expect(
        (screen.getByTestId('chat-bot-url') as HTMLInputElement).value,
      ).toBe('****')
    })
    expect(
      screen.getByTestId('secret-reveal-chat_bot-webhook_url'),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('secret-reveal-chat_bot-secret'),
    ).toBeInTheDocument()
  })

  it('members get no reveal buttons on the edit form', async () => {
    mockMaskedEdit()
    renderSection()
    fireEvent.click(await screen.findByTestId('chat-bot-edit-bot-ding'))
    await waitFor(() => {
      expect(
        (screen.getByTestId('chat-bot-url') as HTMLInputElement).value,
      ).toBe('****')
    })
    expect(
      screen.queryByTestId('secret-reveal-chat_bot-webhook_url'),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByTestId('secret-reveal-chat_bot-secret'),
    ).not.toBeInTheDocument()
  })
})
