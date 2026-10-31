/**
 * Render tests for the Dida365 / TickTick settings card.
 *
 * The card is COLLAPSED by default: the 5-step OAuth wizard is long,
 * and the header's status pill answers "is it connected?" without the
 * user having to expand anything. The body must stay visible while an
 * OAuth flow is in flight or an error is showing — a collapsed header
 * would hide both.
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

import { Dida365Panel } from '../components/settings/Dida365Panel'

const i18nTestInstance = i18n.createInstance()
i18nTestInstance.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

function renderPanel() {
  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <Dida365Panel />
    </I18nextProvider>,
  )
}

const NOT_CONNECTED = {
  connected: false,
  service_type: '',
  project_id: '',
}

const CONNECTED = {
  connected: true,
  service_type: 'dida365',
  project_id: 'proj-1',
}

describe('Dida365Panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockReset()
    mockApi.post.mockReset()
    mockApi.del.mockReset()
  })

  it('renders collapsed by default (no wizard, no Client ID input)', async () => {
    mockApi.get.mockResolvedValue(NOT_CONNECTED)
    renderPanel()

    const toggle = await screen.findByTestId('dida365-toggle')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('dida365-panel')).not.toBeInTheDocument()
    expect(
      screen.queryByTestId('dida365-client-id'),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByTestId('dida365-client-secret'),
    ).not.toBeInTheDocument()
  })

  it('clicking the toggle expands the wizard', async () => {
    mockApi.get.mockResolvedValue(NOT_CONNECTED)
    renderPanel()

    const toggle = await screen.findByTestId('dida365-toggle')
    fireEvent.click(toggle)

    await waitFor(() => {
      expect(toggle).toHaveAttribute('aria-expanded', 'true')
    })
    expect(screen.getByTestId('dida365-panel')).toBeInTheDocument()
    expect(screen.getByTestId('dida365-client-id')).toBeInTheDocument()
    expect(screen.getByTestId('dida365-client-secret')).toBeInTheDocument()
  })

  it('clicking the toggle again collapses it', async () => {
    mockApi.get.mockResolvedValue(NOT_CONNECTED)
    renderPanel()

    const toggle = await screen.findByTestId('dida365-toggle')
    fireEvent.click(toggle)
    await waitFor(() => {
      expect(toggle).toHaveAttribute('aria-expanded', 'true')
    })
    fireEvent.click(toggle)
    await waitFor(() => {
      expect(toggle).toHaveAttribute('aria-expanded', 'false')
    })
    expect(screen.queryByTestId('dida365-panel')).not.toBeInTheDocument()
  })

  it('shows a "not connected" pill when disconnected', async () => {
    mockApi.get.mockResolvedValue(NOT_CONNECTED)
    renderPanel()

    const status = await screen.findByTestId('dida365-status')
    expect(status).toHaveTextContent('Not connected')
  })

  it('shows a green "Connected" pill when connected', async () => {
    mockApi.get.mockResolvedValue(CONNECTED)
    mockApi.get.mockResolvedValueOnce(CONNECTED)
    renderPanel()

    const status = await screen.findByTestId('dida365-status')
    await waitFor(() => {
      expect(status).toHaveTextContent('Connected')
    })
    expect(status).toHaveTextContent('Dida365')
    expect(status.className).toContain('bg-green-100')
  })

  it('stays collapsed when connected (no project picker)', async () => {
    mockApi.get.mockResolvedValue(CONNECTED)
    renderPanel()

    const toggle = await screen.findByTestId('dida365-toggle')
    await waitFor(() => {
      expect(screen.getByTestId('dida365-status')).toHaveTextContent(
        'Connected',
      )
    })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('dida365-panel')).not.toBeInTheDocument()
  })

  it('an error forces the card open', async () => {
    mockApi.get.mockResolvedValue(NOT_CONNECTED)
    // The connect flow fails on the setup step → error is set.
    mockApi.post.mockRejectedValue(new Error('setup exploded'))
    renderPanel()

    const toggle = await screen.findByTestId('dida365-toggle')
    fireEvent.click(toggle)

    // Fill the credentials so the connect button is enabled, then
    // trigger the failing flow.
    await waitFor(() => {
      expect(screen.getByTestId('dida365-panel')).toBeInTheDocument()
    })
    fireEvent.change(screen.getByTestId('dida365-client-id'), {
      target: { value: 'client-id' },
    })
    fireEvent.change(screen.getByTestId('dida365-client-secret'), {
      target: { value: 'client-secret' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: /authorize/i }),
    )

    await waitFor(() => {
      expect(screen.getByText(/setup exploded/)).toBeInTheDocument()
    })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })
})
