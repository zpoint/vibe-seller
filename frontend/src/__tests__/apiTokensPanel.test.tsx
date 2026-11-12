/**
 * ApiTokensPanel: the token list, minting (with the plaintext shown
 * exactly once, alongside copy-paste configs), revoking behind a
 * confirm, and the "no login required" note.
 *
 * The created-token dialog is one card per client with up to two
 * tabs: the GUI route 「界面操作」/"In the app" first and selected
 * where the client has one, the 「命令行 / 配置文件」/"Command line /
 * config file" route otherwise. Only the selected panel is in the
 * DOM, so a visible snippet is always the snippet of the open tab.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import { buildSnippets } from '../lib/apiTokenSnippets'

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

const renderModal = () =>
  render(
    <I18nextProvider i18n={i18nTestInstance}>
      <ApiTokenCreatedModal token="vst_secret" onClose={() => {}} />
    </I18nextProvider>,
  )

/** Each card's tabs, in display order — the selected default is the
 *  first one. Tools without a GUI route never grow an empty tab. */
const TOOL_TABS: Record<string, string[]> = {
  'claude-code': ['cli'],
  codex: ['app', 'cli'],
  workbuddy: ['app'],
  qoder: ['app'],
  hermes: ['app', 'cli'],
  pi: ['cli'],
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

    // The Codex TOML lives on the card's second tab — open it first.
    fireEvent.click(screen.getByTestId('api-token-tab-codex-cli'))
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

    // Hermes opens on its 「In the app」 tab — the connector document
    // its Add-your-own box reads, not the YAML it cannot parse there.
    const hermesJson = screen.getByTestId('api-token-snippet-hermes-json')
    expect(
      JSON.parse(hermesJson.textContent || '{}').mcpServers['vibe-seller']
        .headers.Authorization,
    ).toBe('Bearer vst_secret')
    // The config.yaml route is the card's second tab.
    fireEvent.click(screen.getByTestId('api-token-tab-hermes-cli'))
    const hermes = screen.getByTestId('api-token-snippet-hermes')
    expect(hermes.textContent).toContain('mcp_servers:')
    expect(hermes.textContent).toContain('/api/mcp"')
    expect(hermes.textContent).toContain(
      'Authorization: "Bearer vst_secret"',
    )

    const pi = screen.getByTestId('api-token-snippet-pi')
    expect(pi.textContent).toContain('pi mcp add vibe-seller --url ')
    expect(pi.textContent).toContain('--header "Authorization=Bearer vst_secret"')
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
    fireEvent.click(screen.getByTestId('api-token-tab-codex-cli'))
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

/**
 * Codex desktop adds an MCP server through a form (Settings › Plugins ›
 * Add › Add MCP server, type Streamable HTTP), so its four values have
 * to be copyable one at a time — there is no paste-everything box to
 * hand over. Pinned: the values, that each copy button copies ONLY its
 * own field, and that they follow the edited server address. The card
 * opens on the 「In the app」 tab; the config.toml block is one click
 * away on 「Command line / config file」.
 */
describe('ApiTokenCreatedModal — Codex desktop form', () => {
  let writeText: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.clearAllMocks()
    writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
  })

  it('shows the four fields the form asks for', () => {
    renderModal()
    expect(screen.getByTestId('api-token-codex-form-name').textContent).toBe(
      'vibe-seller',
    )
    expect(screen.getByTestId('api-token-codex-form-url').textContent).toBe(
      'http://localhost:3000/api/mcp',
    )
    expect(
      screen.getByTestId('api-token-codex-form-header-name').textContent,
    ).toBe('Authorization')
    expect(
      screen.getByTestId('api-token-codex-form-header-value').textContent,
    ).toBe('Bearer vst_secret')
  })

  it('copies each field on its own, and marks only that row copied', async () => {
    renderModal()
    fireEvent.click(screen.getByTestId('api-token-copy-codex-form-header-value'))
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('Bearer vst_secret'),
    )
    const copiedButton = screen.getByTestId(
      'api-token-copy-codex-form-header-value',
    )
    expect(copiedButton.textContent).toBe(enTranslation.apiTokens.copied)
    expect(
      screen.getByTestId('api-token-copy-codex-form-url').textContent,
    ).toBe(enTranslation.apiTokens.copy)

    fireEvent.click(screen.getByTestId('api-token-copy-codex-form-url'))
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('http://localhost:3000/api/mcp'),
    )
    expect(writeText).toHaveBeenCalledTimes(2)
  })

  it('follows the edited server address', async () => {
    renderModal()
    fireEvent.change(screen.getByTestId('api-token-server-address'), {
      target: { value: 'http://192.168.1.50:7777' },
    })
    expect(screen.getByTestId('api-token-codex-form-url').textContent).toBe(
      'http://192.168.1.50:7777/api/mcp',
    )
    fireEvent.click(screen.getByTestId('api-token-copy-codex-form-url'))
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('http://192.168.1.50:7777/api/mcp'),
    )
  })

  it('keeps the config.toml route on the card\'s second tab', () => {
    renderModal()
    // The card opens on 「In the app」: the form is shown, the TOML is
    // not in the DOM until its tab is selected.
    expect(screen.getByTestId('api-token-codex-form')).toBeTruthy()
    expect(screen.queryByTestId('api-token-snippet-codex')).toBeNull()
    fireEvent.click(screen.getByTestId('api-token-tab-codex-cli'))
    const toml = screen.getByTestId('api-token-snippet-codex')
    expect(toml.textContent).toContain('[mcp_servers.vibe-seller]')
    expect(toml.textContent).toContain('Bearer vst_secret')
    expect(screen.queryByTestId('api-token-codex-form')).toBeNull()
  })
})

/**
 * Hermes Desktop wires this server up in its connector dialog —
 * Capabilities › Connectors › Add your own — whose box at the top
 * reads a pasted snippet and fills name, type, URL and header from
 * it. That box understands the JSON connector document and NOT the
 * YAML (pasted YAML leaves every field empty, type on STDIO), so the
 * card opens on 「In the app」 with the JSON, and the config.yaml
 * block moves to 「Command line / config file」. Pinned here: the two
 * tabs and what each shows, and that each copy button hands over the
 * full snippet for its own tab.
 */
describe('ApiTokenCreatedModal — Hermes connector dialog', () => {
  let writeText: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.clearAllMocks()
    writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
  })

  it('opens on the in-app tab showing the JSON document', () => {
    renderModal()
    expect(screen.getByTestId('api-token-tab-hermes-app')).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.getByTestId('api-token-panel-hermes-app')).toBeTruthy()
    const json = screen.getByTestId('api-token-snippet-hermes-json')
    const doc = JSON.parse(json.textContent || '{}')
    expect(doc.mcpServers['vibe-seller'].url).toBe(
      'http://localhost:3000/api/mcp',
    )
    // Pasted as-is: the header value carries its "Bearer " prefix.
    expect(doc.mcpServers['vibe-seller'].headers.Authorization).toBe(
      'Bearer vst_secret',
    )
    // Only the selected panel is in the DOM — no YAML behind this tab.
    expect(screen.queryByTestId('api-token-snippet-hermes')).toBeNull()
  })

  it('says where to paste it, and keeps the file route on its tab', () => {
    renderModal()
    expect(
      screen.getByText(enTranslation.apiTokens.hermesAppHint),
    ).toBeTruthy()
    expect(enTranslation.apiTokens.hermesAppHint).toContain(
      'Capabilities › Connectors › Add your own',
    )
    fireEvent.click(screen.getByTestId('api-token-tab-hermes-cli'))
    expect(
      screen.getByText(enTranslation.apiTokens.hermesHint),
    ).toBeTruthy()
    // Native Windows installs keep config.yaml outside ~/.hermes.
    expect(enTranslation.apiTokens.hermesHint).toContain(
      '%LOCALAPPDATA%\\hermes\\config.yaml',
    )
  })

  it('switching tabs swaps JSON for the config.yaml block', () => {
    renderModal()
    fireEvent.click(screen.getByTestId('api-token-tab-hermes-cli'))
    expect(screen.getByTestId('api-token-panel-hermes-cli')).toBeTruthy()
    expect(screen.queryByTestId('api-token-panel-hermes-app')).toBeNull()
    const yaml = screen.getByTestId('api-token-snippet-hermes')
    expect(yaml.textContent).toContain('mcp_servers:')
    expect(yaml.textContent).toContain('Authorization: "Bearer vst_secret"')
    expect(screen.queryByTestId('api-token-snippet-hermes-json')).toBeNull()

    fireEvent.click(screen.getByTestId('api-token-tab-hermes-app'))
    expect(screen.getByTestId('api-token-snippet-hermes-json')).toBeTruthy()
    expect(screen.queryByTestId('api-token-snippet-hermes')).toBeNull()
  })

  it('copies each tab\'s own snippet, byte for byte', async () => {
    renderModal()
    const s = buildSnippets('vst_secret', window.location.origin)
    fireEvent.click(screen.getByTestId('api-token-copy-hermes-json'))
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(s.hermesJson),
    )
    expect(writeText.mock.calls[0][0]).toContain('"Bearer vst_secret"')

    fireEvent.click(screen.getByTestId('api-token-tab-hermes-cli'))
    fireEvent.click(screen.getByTestId('api-token-copy-hermes'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(s.hermes))
    expect(writeText).toHaveBeenCalledTimes(2)
  })

  it('follows the edited server address on both tabs', () => {
    renderModal()
    fireEvent.change(screen.getByTestId('api-token-server-address'), {
      target: { value: 'http://192.168.1.50:7777' },
    })
    const json = screen.getByTestId('api-token-snippet-hermes-json')
    expect(JSON.parse(json.textContent || '{}').mcpServers['vibe-seller'].url)
      .toBe('http://192.168.1.50:7777/api/mcp')
    fireEvent.click(screen.getByTestId('api-token-tab-hermes-cli'))
    expect(screen.getByTestId('api-token-snippet-hermes').textContent)
      .toContain('http://192.168.1.50:7777/api/mcp')
  })
})

describe('ApiTokenCreatedModal — per-tool tabs', () => {
  it('renders one card per client with exactly its tabs', () => {
    renderModal()
    for (const [tool, tabIds] of Object.entries(TOOL_TABS)) {
      expect(screen.getByTestId(`api-token-card-${tool}`)).toBeTruthy()
      for (const id of ['app', 'cli']) {
        const tab = screen.queryByTestId(`api-token-tab-${tool}-${id}`)
        if (tabIds.includes(id)) {
          expect(tab).toBeTruthy()
          // The GUI tab comes first and is the selected default;
          // command-line-only tools default to their single tab.
          expect(tab).toHaveAttribute(
            'aria-selected',
            String(id === tabIds[0]),
          )
        } else {
          // A tool with one route shows one tab — never an empty one.
          expect(tab).toBeNull()
        }
      }
    }
  })

  it('shows only the selected tab\'s content', () => {
    renderModal()
    fireEvent.click(screen.getByTestId('api-token-tab-codex-cli'))
    expect(screen.getByTestId('api-token-panel-codex-cli')).toBeTruthy()
    expect(screen.queryByTestId('api-token-panel-codex-app')).toBeNull()
    fireEvent.click(screen.getByTestId('api-token-tab-codex-app'))
    expect(screen.getByTestId('api-token-panel-codex-app')).toBeTruthy()
    expect(screen.queryByTestId('api-token-panel-codex-cli')).toBeNull()
  })

  it('command-line-only tools show their snippet without a click', () => {
    renderModal()
    expect(screen.getByTestId('api-token-snippet-claude-code').textContent)
      .toContain('claude mcp add')
    expect(screen.getByTestId('api-token-snippet-pi').textContent)
      .toContain('pi mcp add')
    // Claude Code is the last card and carries only its command — no
    // extra note under it.
    const cards = Array.from(
      document.querySelectorAll('[data-testid^="api-token-card-"]'),
    ).map(el => el.getAttribute('data-testid'))
    expect(cards[cards.length - 1]).toBe('api-token-card-claude-code')
    expect(screen.queryByTestId('api-token-claude-code-no-app')).toBeNull()
  })

  it('GUI tools keep their paste-route notes on the open tab', () => {
    renderModal()
    // WorkBuddy: select-all replace is only right for an empty editor.
    expect(screen.getByTestId('api-token-workbuddy-warning').textContent)
      .toContain('vibe-seller')
    // Qoder: where the pasted JSON ends up on disk.
    expect(screen.getByTestId('api-token-qoder-file-note').textContent)
      .toContain('~/.qoder-cn/settings.json')
  })

  it('arrow keys move between the tabs', () => {
    renderModal()
    const appTab = screen.getByTestId('api-token-tab-codex-app')
    appTab.focus()
    fireEvent.keyDown(appTab, { key: 'ArrowRight' })
    const cliTab = screen.getByTestId('api-token-tab-codex-cli')
    expect(cliTab).toHaveAttribute('aria-selected', 'true')
    expect(document.activeElement).toBe(cliTab)

    fireEvent.keyDown(cliTab, { key: 'ArrowLeft' })
    expect(screen.getByTestId('api-token-tab-codex-app')).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })
})

/**
 * The owner rule for everything this dialog hands out: a copied
 * snippet works when pasted, with zero edits — so each copy button
 * must put exactly its snippet on the clipboard (full `Bearer vst_…`
 * header values included, never a bare key).
 */
describe('ApiTokenCreatedModal — every copy button copies its full snippet', () => {
  let writeText: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.clearAllMocks()
    writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
  })

  it('copies each snippet byte-for-byte', async () => {
    renderModal()
    const s = buildSnippets('vst_secret', window.location.origin)

    fireEvent.click(screen.getByTestId('api-token-copy-raw'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(s.raw))

    fireEvent.click(screen.getByTestId('api-token-copy-claude-code'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(s.claudeCode))

    fireEvent.click(screen.getByTestId('api-token-copy-workbuddy'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(s.workbuddy))

    fireEvent.click(screen.getByTestId('api-token-copy-qoder'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(s.qoder))

    fireEvent.click(screen.getByTestId('api-token-copy-hermes-json'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(s.hermesJson))

    // The Hermes config.yaml route sits on the card's second tab;
    // once open, it copies whole as well.
    fireEvent.click(screen.getByTestId('api-token-tab-hermes-cli'))
    fireEvent.click(screen.getByTestId('api-token-copy-hermes'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(s.hermes))

    fireEvent.click(screen.getByTestId('api-token-copy-pi'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(s.pi))

    // The TOML needs its tab open; then it copies whole too.
    fireEvent.click(screen.getByTestId('api-token-tab-codex-cli'))
    fireEvent.click(screen.getByTestId('api-token-copy-codex'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(s.codex))
  })

  it('copies the Codex header value with its "Bearer " prefix', async () => {
    renderModal()
    fireEvent.click(
      screen.getByTestId('api-token-copy-codex-form-header-value'),
    )
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('Bearer vst_secret'),
    )
    expect(writeText.mock.calls[0][0]).toMatch(/^Bearer vst_/)
  })
})
