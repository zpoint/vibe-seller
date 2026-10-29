/**
 * The Ads API section is pinned to the top of Settings → Integrations,
 * so it is the first thing an operator sees. It must also be a card
 * like the sections below it, not loose text on the page background.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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

import { IntegrationsPanel } from '../components/settings/IntegrationsPanel'

const i18nTestInstance = i18n.createInstance()
i18nTestInstance.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

/** The Google Workspace card has no testid, so find it through its
 *  title and walk up to the card that contains it. */
function googleWorkspaceCard(): HTMLElement {
  const heading = screen.getByRole('heading', {
    name: enTranslation.integrations.gws.title,
  })
  const card = heading.closest('div.bg-white')
  expect(card, 'Google Workspace card not found').toBeTruthy()
  return card as HTMLElement
}

describe('IntegrationsPanel order', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/settings/google-workspace/status') {
        return Promise.resolve({
          binary: false,
          auth: false,
          version: null,
          enabled: false,
          installed: false,
        })
      }
      return Promise.resolve([])
    })
    mockApi.post.mockImplementation((url: string) => {
      if (url === '/api/ads/stores/sync') return Promise.resolve({ stores: [] })
      return Promise.resolve({})
    })
  })

  it('puts the ads card first, above Google Workspace', async () => {
    render(
      <I18nextProvider i18n={i18nTestInstance}>
        <IntegrationsPanel />
      </I18nextProvider>,
    )

    const adsCard = await screen.findByTestId('ads-card')
    const gwsCard = googleWorkspaceCard()

    // DOCUMENT_POSITION_FOLLOWING on the ads card means the Google
    // Workspace card comes after it in document order.
    expect(adsCard.compareDocumentPosition(gwsCard) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy()

    // ...and the ads card is the panel's very first child, so nothing
    // else can sneak above it.
    expect(adsCard.parentElement?.firstElementChild).toBe(adsCard)
  })

  it('gives the ads card the same shell as the other integration cards', async () => {
    render(
      <I18nextProvider i18n={i18nTestInstance}>
        <IntegrationsPanel />
      </I18nextProvider>,
    )

    const adsCard = await screen.findByTestId('ads-card')
    const gwsCard = googleWorkspaceCard()
    for (const cls of ['bg-white', 'rounded-lg', 'border', 'border-gray-200', 'p-4']) {
      expect(adsCard.className.split(' ')).toContain(cls)
      expect(gwsCard.className.split(' ')).toContain(cls)
    }
  })
})
