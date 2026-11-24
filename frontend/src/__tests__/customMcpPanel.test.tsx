/**
 * Settings → Integrations → Custom MCP servers.
 *
 * Admins paste a vendor's config as-is (the server normalizes it); a
 * save is followed by a connection test without another click; a name
 * that exists asks before replacing. Members see the list only.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'

const t = enTranslation.customMcp

const mockApi = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
}))
vi.mock('../api', () => ({ api: mockApi }))

import { CustomMcpPanel } from '../components/settings/CustomMcpPanel'

const i18nTest = i18n.createInstance()
i18nTest.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

const SERVER = {
  id: 's1',
  name: 'acme-data',
  type: 'http',
  url: 'https://mcp.example.com/mcp',
  headers: { Authorization: '****abcd' },
  enabled: true,
  valid: true,
  problem: null,
}

function renderPanel(isAdmin: boolean) {
  return render(
    <I18nextProvider i18n={i18nTest}>
      <CustomMcpPanel isAdmin={isAdmin} />
    </I18nextProvider>,
  )
}

describe('CustomMcpPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockResolvedValue([])
  })

  it('shows the masked credential, never asks for more', async () => {
    mockApi.get.mockResolvedValue([SERVER])
    renderPanel(true)
    const row = await screen.findByTestId('custom-mcp-row-acme-data')
    expect(row).toHaveTextContent('https://mcp.example.com/mcp')
    expect(row).toHaveTextContent('Authorization: ****abcd')
    expect(row).toHaveTextContent('HTTP')
  })

  it('saves the paste as-is, then tests the connection by itself', async () => {
    mockApi.post.mockImplementation((url: string) => {
      if (url === '/api/custom-mcp-servers') return Promise.resolve([SERVER])
      return Promise.resolve({
        ok: true, server_name: 'acme', tool_count: 3,
        tools: [{ name: 'lookup', description: 'Look things up' }],
      })
    })
    renderPanel(true)
    fireEvent.click(await screen.findByTestId('custom-mcp-add'))
    const paste = 'install this: {"mcpServers": {"acme-data": {"url": "https://mcp.example.com/mcp"}}}'
    fireEvent.change(screen.getByTestId('custom-mcp-config'), { target: { value: paste } })
    mockApi.get.mockResolvedValue([SERVER])
    fireEvent.click(screen.getByTestId('custom-mcp-save'))

    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      '/api/custom-mcp-servers',
      { config: paste, name: null, replace: false },
    ))
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      '/api/custom-mcp-servers/s1/test', undefined, expect.anything(),
    ))
    const result = await screen.findByTestId('custom-mcp-test-result')
    expect(result).toHaveTextContent('Connected to acme — 3 tools')
  })

  it('asks before replacing a server with the same name', async () => {
    const conflict = Object.assign(new Error('exists'), {
      status: 409, detail: { names: ['acme-data'] },
    })
    mockApi.post
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce([SERVER])
      .mockResolvedValue({ ok: true, tool_count: 0, tools: [] })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPanel(true)
    fireEvent.click(await screen.findByTestId('custom-mcp-add'))
    fireEvent.change(screen.getByTestId('custom-mcp-config'), {
      target: { value: 'https://mcp.example.com/mcp' },
    })
    fireEvent.change(screen.getByTestId('custom-mcp-name'), {
      target: { value: 'acme-data' },
    })
    fireEvent.click(screen.getByTestId('custom-mcp-save'))
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      '/api/custom-mcp-servers',
      { config: 'https://mcp.example.com/mcp', name: 'acme-data', replace: true },
    ))
    expect(confirm).toHaveBeenCalled()
    confirm.mockRestore()
  })

  it('shows why a paste was refused', async () => {
    mockApi.post.mockRejectedValue(Object.assign(
      new Error('headers.Authorization uses a ${…} variable'), { status: 400 },
    ))
    renderPanel(true)
    fireEvent.click(await screen.findByTestId('custom-mcp-add'))
    fireEvent.change(screen.getByTestId('custom-mcp-config'), { target: { value: '{}' } })
    fireEvent.click(screen.getByTestId('custom-mcp-save'))
    expect(await screen.findByTestId('custom-mcp-error')).toHaveTextContent('variable')
  })

  it('turns a server off and deletes it', async () => {
    mockApi.get.mockResolvedValue([SERVER])
    mockApi.patch.mockResolvedValue({ ...SERVER, enabled: false })
    mockApi.del.mockResolvedValue({ ok: true })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPanel(true)
    const row = await screen.findByTestId('custom-mcp-row-acme-data')
    fireEvent.click(within(row).getByTestId('custom-mcp-toggle'))
    await waitFor(() => expect(mockApi.patch).toHaveBeenCalledWith(
      '/api/custom-mcp-servers/s1', { enabled: false },
    ))
    fireEvent.click(within(row).getByTestId('custom-mcp-delete'))
    await waitFor(() => expect(mockApi.del).toHaveBeenCalledWith('/api/custom-mcp-servers/s1'))
    confirm.mockRestore()
  })

  it('gives members the list but no controls', async () => {
    mockApi.get.mockResolvedValue([SERVER])
    renderPanel(false)
    const row = await screen.findByTestId('custom-mcp-row-acme-data')
    expect(screen.queryByTestId('custom-mcp-add')).toBeNull()
    expect(within(row).queryByTestId('custom-mcp-test')).toBeNull()
    expect(within(row).queryByTestId('custom-mcp-delete')).toBeNull()
    expect(screen.getByText(t.adminOnly)).toBeInTheDocument()
  })
})
