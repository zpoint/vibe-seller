/**
 * The API-key card is pinned to the top of Settings → Integrations —
 * it is what MCP clients need — and the Ads API section stays the
 * first integration below it. Both must be cards like the sections
 * below, not loose text on the page background.
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
const mockApiTokens = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  revoke: vi.fn(),
}))
vi.mock('../api', () => ({ api: mockApi, apiTokens: mockApiTokens }))

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

/** DOCUMENT_POSITION_FOLLOWING on `before` means `after` comes after
 *  it in document order. */
function expectBefore(before: HTMLElement, after: HTMLElement) {
  expect(before.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING)
    .toBeTruthy()
}

describe('IntegrationsPanel order', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApiTokens.list.mockResolvedValue([])
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

  it('puts the ads card first, then the API-key card', async () => {
    render(
      <I18nextProvider i18n={i18nTestInstance}>
        <IntegrationsPanel />
      </I18nextProvider>,
    )

    const keysCard = await screen.findByTestId('api-keys-card')
    const adsCard = await screen.findByTestId('ads-card')
    expectBefore(adsCard, keysCard)

    // ...and the ads card is the panel's very first child, so nothing
    // else can sneak above it.
    expect(adsCard.parentElement?.firstElementChild).toBe(adsCard)
  })

  it('keeps the ads card above Google Workspace', async () => {
    render(
      <I18nextProvider i18n={i18nTestInstance}>
        <IntegrationsPanel />
      </I18nextProvider>,
    )

    const adsCard = await screen.findByTestId('ads-card')
    const gwsCard = googleWorkspaceCard()
    expectBefore(adsCard, gwsCard)
  })

  it('gives the keys and ads cards the same shell as the other integration cards', async () => {
    render(
      <I18nextProvider i18n={i18nTestInstance}>
        <IntegrationsPanel />
      </I18nextProvider>,
    )

    const keysCard = await screen.findByTestId('api-keys-card')
    const adsCard = await screen.findByTestId('ads-card')
    const gwsCard = googleWorkspaceCard()
    for (const card of [keysCard, adsCard, gwsCard]) {
      for (const cls of ['bg-white', 'rounded-lg', 'border', 'border-gray-200', 'p-4']) {
        expect(card.className.split(' ')).toContain(cls)
      }
    }
  })
})
