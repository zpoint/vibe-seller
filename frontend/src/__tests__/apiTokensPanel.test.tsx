/**
 * ApiTokensPanel: the token list, minting (with the plaintext shown
 * exactly once, alongside copy-paste configs), revoking behind a
 * confirm, and the "no login required" note.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'

const mockApi = vi.hoisted(() => ({
  apiTokens: {
    list: vi.fn(),
    create: vi.fn(),
    revoke: vi.fn(),
  },
}))
vi.mock('../api', () => mockApi)

import { ApiTokensPanel } from '../components/settings/ApiTokensPanel'
import { ApiTokenCreatedModal } from '../components/settings/ApiTokenCreatedModal'

const i18nTestInstance = i18n.createInstance()
i18nTestInstance.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

const TOKEN = {
  id: 'tok-1',
  name: 'laptop',
  prefix: 'vst_abc123',
  created_at: '2026-01-02T03:04:05Z',
  last_used_at: null,
  expires_at: null,
}

function renderPanel(authRequired = true) {
  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <ApiTokensPanel authRequired={authRequired} />
    </I18nextProvider>,
  )
}

describe('ApiTokensPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.apiTokens.list.mockResolvedValue([])
    mockApi.apiTokens.create.mockResolvedValue({ ...TOKEN, token: 'vst_secret' })
    mockApi.apiTokens.revoke.mockResolvedValue(undefined)
  })

  it('lists the tokens the server returns', async () => {
    mockApi.apiTokens.list.mockResolvedValue([TOKEN])
    renderPanel()
    expect(await screen.findByText('laptop')).toBeTruthy()
    expect(screen.getByText('vst_abc123')).toBeTruthy()
    expect(mockApi.apiTokens.list).toHaveBeenCalledTimes(1)
  })

  it('says when a token was never used and never expires', async () => {
    mockApi.apiTokens.list.mockResolvedValue([TOKEN])
    renderPanel()
    const row = await screen.findByTestId('api-token-row-tok-1')
    expect(row.textContent).toContain('Never')
  })

  it('shows the no-login note only when the server does not require auth', async () => {
    const { unmount } = renderPanel(true)
    await waitFor(() => expect(mockApi.apiTokens.list).toHaveBeenCalled())
    expect(screen.queryByTestId('api-tokens-auth-off-note')).toBeNull()
    unmount()

    renderPanel(false)
    expect(await screen.findByTestId('api-tokens-auth-off-note')).toBeTruthy()
  })

  it('mints with a never-expiring default', async () => {
    renderPanel()
    fireEvent.click(await screen.findByTestId('api-tokens-create'))
    fireEvent.change(screen.getByTestId('api-tokens-name'), {
      target: { value: 'laptop' },
    })
    fireEvent.click(screen.getByTestId('api-tokens-submit'))
    await waitFor(() =>
      expect(mockApi.apiTokens.create).toHaveBeenCalledWith('laptop', null),
    )
  })

  it('sends the picked expiry in days', async () => {
    renderPanel()
    fireEvent.click(await screen.findByTestId('api-tokens-create'))
    fireEvent.change(screen.getByTestId('api-tokens-name'), {
      target: { value: 'laptop' },
    })
    fireEvent.change(screen.getByTestId('api-tokens-expiry'), {
      target: { value: '90' },
    })
    fireEvent.click(screen.getByTestId('api-tokens-submit'))
    await waitFor(() =>
      expect(mockApi.apiTokens.create).toHaveBeenCalledWith('laptop', 90),
    )
  })

  it('will not mint a nameless token', async () => {
    renderPanel()
    fireEvent.click(await screen.findByTestId('api-tokens-create'))
    expect(screen.getByTestId('api-tokens-submit')).toBeDisabled()
    fireEvent.click(screen.getByTestId('api-tokens-submit'))
    expect(mockApi.apiTokens.create).not.toHaveBeenCalled()
  })

  it('shows the plaintext once, with a config for every client', async () => {
    renderPanel()
    fireEvent.click(await screen.findByTestId('api-tokens-create'))
    fireEvent.change(screen.getByTestId('api-tokens-name'), {
      target: { value: 'laptop' },
    })
    fireEvent.click(screen.getByTestId('api-tokens-submit'))

    const raw = await screen.findByTestId('api-token-snippet-raw')
    expect(raw.textContent).toBe('vst_secret')

    const claude = screen.getByTestId('api-token-snippet-claude-code')
    expect(claude.textContent).toContain(
      'claude mcp add --scope user --transport http',
    )
    expect(claude.textContent).toContain('/api/mcp')
    expect(claude.textContent).toContain('Authorization: Bearer vst_secret')

    const codex = screen.getByTestId('api-token-snippet-codex')
    expect(codex.textContent).toContain('[mcp_servers.vibe-seller]')
    expect(codex.textContent).toContain(
      'http_headers = { "Authorization" = "Bearer vst_secret" }',
    )
    expect(codex.textContent).not.toContain('export')
    expect(codex.textContent).not.toContain('bearer_token_env_var')

    const workbuddy = screen.getByTestId('api-token-snippet-workbuddy')
    const parsed = JSON.parse(workbuddy.textContent || '{}')
    expect(parsed.mcpServers['vibe-seller'].headers.Authorization).toBe(
      'Bearer vst_secret',
    )
    expect(parsed.mcpServers['vibe-seller'].url).toContain('/api/mcp')

    const qoder = screen.getByTestId('api-token-snippet-qoder')
    const qoderParsed = JSON.parse(qoder.textContent || '{}')
    expect(qoderParsed.mcpServers['vibe-seller'].type).toBe('http')
    expect(qoderParsed.mcpServers['vibe-seller'].headers.Authorization).toBe(
      'Bearer vst_secret',
    )
    expect(qoderParsed.mcpServers['vibe-seller'].url).toContain('/api/mcp')
  })

  it('drops the plaintext when the dialog is closed', async () => {
    renderPanel()
    fireEvent.click(await screen.findByTestId('api-tokens-create'))
    fireEvent.change(screen.getByTestId('api-tokens-name'), {
      target: { value: 'laptop' },
    })
    fireEvent.click(screen.getByTestId('api-tokens-submit'))
    await screen.findByTestId('api-token-snippet-raw')
    fireEvent.click(screen.getByText(enTranslation.apiTokens.done))
    await waitFor(() =>
      expect(screen.queryByTestId('api-token-snippet-raw')).toBeNull(),
    )
  })

  it('asks before revoking, and cancel sends nothing', async () => {
    mockApi.apiTokens.list.mockResolvedValue([TOKEN])
    renderPanel()
    fireEvent.click(await screen.findByTestId('api-token-revoke-tok-1'))
    expect(screen.getByTestId('api-token-revoke-confirm')).toBeTruthy()
    fireEvent.click(screen.getByTestId('api-token-revoke-cancel'))
    expect(screen.queryByTestId('api-token-revoke-confirm')).toBeNull()
    expect(mockApi.apiTokens.revoke).not.toHaveBeenCalled()
  })

  it('revokes once confirmed', async () => {
    mockApi.apiTokens.list.mockResolvedValue([TOKEN])
    renderPanel()
    fireEvent.click(await screen.findByTestId('api-token-revoke-tok-1'))
    fireEvent.click(screen.getByTestId('api-token-revoke-confirm-button'))
    await waitFor(() =>
      expect(mockApi.apiTokens.revoke).toHaveBeenCalledWith('tok-1'),
    )
  })
})

describe('ApiTokenCreatedModal — keyboard focus', () => {
  const renderModal = () =>
    render(
      <I18nextProvider i18n={i18nTestInstance}>
        <ApiTokenCreatedModal token="vst_secret" onClose={() => {}} />
      </I18nextProvider>,
    )

  it('takes focus on open and gives it back on close', () => {
    const opener = document.createElement('button')
    document.body.appendChild(opener)
    opener.focus()
    const { unmount } = renderModal()
    expect(document.activeElement).toBe(
      screen.getByTestId('api-token-created-panel'),
    )
    unmount()
    expect(document.activeElement).toBe(opener)
    opener.remove()
  })

  it('keeps Tab and Shift+Tab inside the dialog', () => {
    renderModal()
    const panel = screen.getByTestId('api-token-created-panel')
    const focusable = panel.querySelectorAll<HTMLElement>('button, input')
    const first = focusable[0]
    const last = focusable[focusable.length - 1]

    last.focus()
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(document.activeElement).toBe(first)

    fireEvent.keyDown(first, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
  })
})

describe('ApiTokensPanel — editable server address', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.apiTokens.list.mockResolvedValue([])
    mockApi.apiTokens.create.mockResolvedValue({ ...TOKEN, token: 'vst_secret' })
    mockApi.apiTokens.revoke.mockResolvedValue(undefined)
  })

  async function openCreatedModal() {
    renderPanel()
    fireEvent.click(await screen.findByTestId('api-tokens-create'))
    fireEvent.change(screen.getByTestId('api-tokens-name'), {
      target: { value: 'laptop' },
    })
    fireEvent.click(screen.getByTestId('api-tokens-submit'))
    await screen.findByTestId('api-token-snippet-raw')
  }

  it('edits the address every snippet points at', async () => {
    await openCreatedModal()
    const field = screen.getByTestId('api-token-server-address')
    // jsdom serves the page at localhost:3000 — the loopback case.
    fireEvent.change(field, { target: { value: 'http://192.168.1.50:7777' } })
    const claude = screen.getByTestId('api-token-snippet-claude-code')
    expect(claude.textContent).toContain('http://192.168.1.50:7777/api/mcp')
    expect(claude.textContent).not.toContain('localhost')
    const codex = screen.getByTestId('api-token-snippet-codex')
    expect(codex.textContent).toContain('http://192.168.1.50:7777/api/mcp')
  })

  it('warns for a loopback address and clears when edited away', async () => {
    await openCreatedModal()
    // Prefill is window.location.origin (localhost in jsdom) → warned.
    expect(screen.getByTestId('api-token-loopback-warning')).toBeTruthy()
    fireEvent.change(screen.getByTestId('api-token-server-address'), {
      target: { value: 'https://vibe.example.com' },
    })
    expect(screen.queryByTestId('api-token-loopback-warning')).toBeNull()
  })
})
