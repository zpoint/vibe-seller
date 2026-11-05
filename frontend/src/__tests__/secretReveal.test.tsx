/**
 * Tests for SecretReveal — the admin "Show / Copy full key" action.
 *
 * Every edit form shows stored secrets only as a mask; this is the
 * one component that fetches the real value through
 * POST /api/secrets/reveal. Pinned here:
 * - Show reveals the value inline and hides it again on toggle;
 * - Copy fetches the value and copies it WITHOUT showing it;
 * - a failed reveal (403/404) surfaces the error, no value;
 * - the copy falls back to the shared clipboard helper.
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
  revealSecret: vi.fn(),
}))
vi.mock('../api', () => ({ ...mockApi, api: mockApi }))

import { SecretReveal } from '../components/SecretReveal'

const i18nTestInstance = i18n.createInstance()
i18nTestInstance.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

function renderReveal(props: Record<string, unknown> = {}) {
  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <SecretReveal resource="profile" id="minimax" field="ANTHROPIC_AUTH_TOKEN" {...props} />
    </I18nextProvider>,
  )
}

describe('SecretReveal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('Show fetches the value and reveals it inline, then hides it', async () => {
    mockApi.revealSecret.mockResolvedValueOnce({
      resource: 'profile',
      id: 'minimax',
      field: 'ANTHROPIC_AUTH_TOKEN',
      value: 'sk-live-minimax-abcd',
    })
    renderReveal()

    fireEvent.click(screen.getByRole('button', { name: 'Show' }))
    const shown = await screen.findByTestId(
      'secret-revealed-profile-ANTHROPIC_AUTH_TOKEN',
    )
    expect(shown).toHaveTextContent('sk-live-minimax-abcd')
    expect(mockApi.revealSecret).toHaveBeenCalledWith(
      'profile',
      'minimax',
      'ANTHROPIC_AUTH_TOKEN',
    )

    fireEvent.click(screen.getByRole('button', { name: 'Hide' }))
    expect(
      screen.queryByTestId('secret-revealed-profile-ANTHROPIC_AUTH_TOKEN'),
    ).not.toBeInTheDocument()
  })

  it('Copy fetches the value and copies it without showing it', async () => {
    mockApi.revealSecret.mockResolvedValueOnce({
      resource: 'profile',
      id: 'minimax',
      field: 'ANTHROPIC_AUTH_TOKEN',
      value: 'sk-live-minimax-abcd',
    })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    renderReveal()

    fireEvent.click(screen.getByRole('button', { name: 'Copy' }))
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('sk-live-minimax-abcd')
    })
    // copied feedback appears, the value itself is never rendered
    expect(await screen.findByText('Copied')).toBeInTheDocument()
    expect(
      screen.queryByTestId('secret-revealed-profile-ANTHROPIC_AUTH_TOKEN'),
    ).not.toBeInTheDocument()
  })

  it('a refused reveal surfaces the error and never a value', async () => {
    mockApi.revealSecret.mockRejectedValueOnce(
      new Error('Admin access required'),
    )
    renderReveal()

    fireEvent.click(screen.getByRole('button', { name: 'Show' }))
    await waitFor(() => {
      expect(screen.getByText('Admin access required')).toBeInTheDocument()
    })
    expect(
      screen.queryByTestId('secret-revealed-profile-ANTHROPIC_AUTH_TOKEN'),
    ).not.toBeInTheDocument()
  })
})
