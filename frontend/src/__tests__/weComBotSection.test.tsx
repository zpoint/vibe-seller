/**
 * Render tests for WeComBotSection.
 *
 * Mocks the `api` helper so the component's list/create/update/
 * delete/test flows can be exercised end-to-end in jsdom. We
 * verify the visible UI state (frontend status) after each
 * interaction, matching the backend CRUD lifecycle.
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

import { WeComBotSection } from '../components/WeComBotSection'

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
      <WeComBotSection />
    </I18nextProvider>,
  )
}

const BOT_A_SUMMARY = {
  id: 'bot-a',
  name: 'Ops Alerts',
  webhook_url_masked: 'https://qyapi.weixin.qq.com/...?key=****abcd',
  created_at: '2026-04-17T10:00:00Z',
  updated_at: '2026-04-17T10:00:00Z',
}

const BOT_A_FULL = {
  id: 'bot-a',
  name: 'Ops Alerts',
  webhook_url: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=real-abcd',
  created_at: '2026-04-17T10:00:00Z',
  updated_at: '2026-04-17T10:00:00Z',
}

describe('WeComBotSection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockReset()
    mockApi.post.mockReset()
    mockApi.put.mockReset()
    mockApi.del.mockReset()
    // Silence window.confirm
    window.confirm = vi.fn(() => true)
  })

  it('renders empty state after list returns []', async () => {
    mockApi.get.mockResolvedValueOnce([])
    renderSection()

    expect(mockApi.get).toHaveBeenCalledWith('/api/wecom-bots')
    expect(await screen.findByTestId('wecom-bot-empty')).toBeInTheDocument()
    expect(screen.queryByTestId('wecom-bot-list')).not.toBeInTheDocument()
  })

  it('renders list rows with masked URL (key hidden)', async () => {
    mockApi.get.mockResolvedValueOnce([BOT_A_SUMMARY])
    renderSection()

    const row = await screen.findByTestId('wecom-bot-row-bot-a')
    expect(row).toHaveTextContent('Ops Alerts')
    expect(row).toHaveTextContent('qyapi.weixin.qq.com')
    // Masked form only — raw secret key must not appear
    expect(row).toHaveTextContent('****abcd')
    expect(row.textContent).not.toMatch(/real-abcd/)
  })

  it('opens the form on Add, calls POST, then refreshes list', async () => {
    mockApi.get.mockResolvedValueOnce([])
    mockApi.post.mockResolvedValueOnce(BOT_A_FULL)
    mockApi.get.mockResolvedValueOnce([BOT_A_SUMMARY]) // refresh

    renderSection()
    await screen.findByTestId('wecom-bot-empty')

    fireEvent.click(screen.getByTestId('wecom-bot-add'))
    fireEvent.change(screen.getByTestId('wecom-bot-input-name'), {
      target: { value: 'Ops Alerts' },
    })
    fireEvent.change(screen.getByTestId('wecom-bot-input-url'), {
      target: { value: BOT_A_FULL.webhook_url },
    })
    fireEvent.click(screen.getByTestId('wecom-bot-save'))

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/api/wecom-bots', {
        name: 'Ops Alerts',
        webhook_url: BOT_A_FULL.webhook_url,
      })
    })
    await screen.findByTestId('wecom-bot-row-bot-a')
    expect(screen.queryByTestId('wecom-bot-form')).not.toBeInTheDocument()
  })

  it('save is blocked when name or url blank (no API call)', async () => {
    mockApi.get.mockResolvedValueOnce([])
    renderSection()
    await screen.findByTestId('wecom-bot-empty')

    fireEvent.click(screen.getByTestId('wecom-bot-add'))
    fireEvent.click(screen.getByTestId('wecom-bot-save'))

    const toast = await screen.findByTestId('wecom-bot-toast')
    expect(toast.className).toContain('text-red-600')
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('Edit fetches full URL via single GET and pre-fills the form', async () => {
    mockApi.get.mockResolvedValueOnce([BOT_A_SUMMARY]) // list
    mockApi.get.mockResolvedValueOnce(BOT_A_FULL) // single GET
    mockApi.put.mockResolvedValueOnce({ ...BOT_A_FULL, name: 'Renamed' })
    mockApi.get.mockResolvedValueOnce([{ ...BOT_A_SUMMARY, name: 'Renamed' }])

    renderSection()
    fireEvent.click(await screen.findByTestId('wecom-bot-edit-bot-a'))

    // After the single GET resolves, the full URL shows up in the
    // edit form (not the masked one).
    await waitFor(() => {
      const urlInput = screen.getByTestId('wecom-bot-input-url') as HTMLInputElement
      expect(urlInput.value).toBe(BOT_A_FULL.webhook_url)
    })
    expect(mockApi.get).toHaveBeenCalledWith('/api/wecom-bots/bot-a')

    const nameInput = screen.getByTestId('wecom-bot-input-name') as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'Renamed' } })
    fireEvent.click(screen.getByTestId('wecom-bot-save'))

    await waitFor(() => {
      expect(mockApi.put).toHaveBeenCalledWith('/api/wecom-bots/bot-a', {
        name: 'Renamed',
        webhook_url: BOT_A_FULL.webhook_url,
      })
    })
    const row = await screen.findByTestId('wecom-bot-row-bot-a')
    expect(row).toHaveTextContent('Renamed')
  })

  it('Delete calls DEL after confirm and refreshes', async () => {
    mockApi.get.mockResolvedValueOnce([BOT_A_SUMMARY])
    mockApi.del.mockResolvedValueOnce({ ok: true })
    mockApi.get.mockResolvedValueOnce([])

    renderSection()
    fireEvent.click(await screen.findByTestId('wecom-bot-delete-bot-a'))

    await waitFor(() => {
      expect(mockApi.del).toHaveBeenCalledWith('/api/wecom-bots/bot-a')
    })
    await screen.findByTestId('wecom-bot-empty')
    expect(screen.queryByTestId('wecom-bot-row-bot-a')).not.toBeInTheDocument()
  })

  it('Delete aborts if user cancels the confirm dialog', async () => {
    mockApi.get.mockResolvedValueOnce([BOT_A_SUMMARY])
    window.confirm = vi.fn(() => false)

    renderSection()
    fireEvent.click(await screen.findByTestId('wecom-bot-delete-bot-a'))

    expect(mockApi.del).not.toHaveBeenCalled()
  })

  it('Test button posts to /test and shows success toast on ok=true', async () => {
    mockApi.get.mockResolvedValueOnce([BOT_A_SUMMARY])
    mockApi.post.mockResolvedValueOnce({ ok: true, message: 'Message sent' })

    renderSection()
    fireEvent.click(await screen.findByTestId('wecom-bot-test-bot-a'))

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        '/api/wecom-bots/bot-a/test',
        {},
      )
    })
    const toast = await screen.findByTestId('wecom-bot-toast')
    expect(toast.className).toContain('text-green-700')
  })

  it('Test button shows error toast on ok=false with server message', async () => {
    mockApi.get.mockResolvedValueOnce([BOT_A_SUMMARY])
    mockApi.post.mockResolvedValueOnce({
      ok: false,
      message: 'invalid webhook url',
    })

    renderSection()
    fireEvent.click(await screen.findByTestId('wecom-bot-test-bot-a'))

    const toast = await screen.findByTestId('wecom-bot-toast')
    expect(toast.className).toContain('text-red-600')
    expect(toast.textContent).toMatch(/invalid webhook url/)
  })

  it('surfaces list-fetch error in the error banner', async () => {
    mockApi.get.mockRejectedValueOnce(new Error('boom'))
    renderSection()

    const err = await screen.findByTestId('wecom-bot-error')
    expect(err.textContent).toContain('boom')
  })
})
