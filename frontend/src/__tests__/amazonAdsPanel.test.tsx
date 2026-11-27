/**
 * AmazonAdsPanel: no key to enter, every store listed, revoking an
 * authorization asks first (with an opt-in to delete the history), and
 * the recheck after a consent names why the store did not bind.
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

import { AmazonAdsPanel } from '../components/settings/AmazonAdsPanel'

const i18nTestInstance = i18n.createInstance()
i18nTestInstance.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

const AUTHORIZED = [
  { local_id: 's1', name: 'Shop A', marketplace: 'SA', store_key: 'sk_sa', authorized: true },
  { local_id: 's1', name: 'Shop A', marketplace: 'AE', store_key: 'sk_ae', authorized: true },
]
const NOT_AUTHORIZED = [
  { local_id: 's2', name: 'Shop B', marketplace: null, store_key: null, authorized: false },
]

function renderPanel() {
  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <AmazonAdsPanel />
    </I18nextProvider>,
  )
}

describe('AmazonAdsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.post.mockImplementation((url: string) => {
      if (url === '/api/ads/stores/sync') {
        return Promise.resolve({ stores: [...AUTHORIZED, ...NOT_AUTHORIZED] })
      }
      return Promise.resolve({ stores: NOT_AUTHORIZED, kept_shared: [] })
    })
  })

  it('asks for no key and lists every store straight away', async () => {
    renderPanel()
    await screen.findByTestId('ads-stores')
    expect(screen.queryByTestId('ads-api-key')).toBeNull()
    expect(screen.queryByTestId('ads-save')).toBeNull()
    expect(mockApi.put).not.toHaveBeenCalled()
    expect(screen.getByText('Shop A')).toBeTruthy()
    expect(screen.getByText('Shop B')).toBeTruthy()
    expect(screen.getByText('SA · AE')).toBeTruthy()
  })

  it('is a card like every other integration', async () => {
    renderPanel()
    const card = await screen.findByTestId('ads-card')
    expect(card.className).toContain('bg-white')
    expect(card.className).toContain('rounded-lg')
    expect(card.className).toContain('border')
    expect(card.className).toContain('border-gray-200')
    expect(card.className).toContain('p-4')
    // The card title reads like the other integration titles, not as
    // loose text on the page background.
    const title = screen.getByRole('heading', { name: enTranslation.ads.title })
    expect(title.className).toContain('font-semibold')
  })

  it('says it is Amazon only', async () => {
    renderPanel()
    await screen.findByTestId('ads-stores')
    expect(screen.getByText(/Amazon stores only/)).toBeTruthy()
  })

  it('walks through authorizing in three steps, ending on the button it names', async () => {
    renderPanel()
    await screen.findByTestId('ads-stores')
    const steps = screen.getByTestId('ads-steps').querySelectorAll('li')
    expect(steps).toHaveLength(3)
    expect(steps[0].textContent).toMatch(/Authorize/)
    expect(steps[1].textContent).toMatch(/Ziniao/)
    expect(steps[2].textContent).toContain(enTranslation.ads.recheck)
    expect(screen.queryByText(/no key to enter/)).toBeNull()
  })

  it('offers revoke only on an authorized store', async () => {
    renderPanel()
    await screen.findByTestId('ads-stores')
    expect(screen.getByTestId('ads-unbind-s1')).toBeTruthy()
    expect(screen.queryByTestId('ads-unbind-s2')).toBeNull()
  })

  it('asks before revoking, and cancel sends nothing', async () => {
    renderPanel()
    fireEvent.click(await screen.findByTestId('ads-unbind-s1'))
    expect(screen.getByTestId('ads-unbind-confirm')).toBeTruthy()
    fireEvent.click(screen.getByTestId('ads-unbind-cancel'))
    expect(screen.queryByTestId('ads-unbind-confirm')).toBeNull()
    expect(mockApi.post).toHaveBeenCalledTimes(1) // the initial sync only
  })

  it('revokes keeping the history by default', async () => {
    renderPanel()
    fireEvent.click(await screen.findByTestId('ads-unbind-s1'))
    fireEvent.click(screen.getByTestId('ads-unbind-confirm-button'))
    await waitFor(() =>
      expect(mockApi.post).toHaveBeenCalledWith('/api/ads/stores/s1/unbind', { purge: false }),
    )
    expect(await screen.findByTestId('ads-message')).toBeTruthy()
    await waitFor(() => expect(screen.queryByTestId('ads-unbind-s1')).toBeNull())
  })

  it('deletes the history only when asked to', async () => {
    renderPanel()
    fireEvent.click(await screen.findByTestId('ads-unbind-s1'))
    fireEvent.click(screen.getByTestId('ads-unbind-purge'))
    fireEvent.click(screen.getByTestId('ads-unbind-confirm-button'))
    await waitFor(() =>
      expect(mockApi.post).toHaveBeenCalledWith('/api/ads/stores/s1/unbind', { purge: true }),
    )
  })

  it('says when the history was kept for another machine', async () => {
    mockApi.post.mockImplementation((url: string) => {
      if (url === '/api/ads/stores/sync') return Promise.resolve({ stores: AUTHORIZED })
      return Promise.resolve({ stores: [], kept_shared: ['sk_sa'] })
    })
    renderPanel()
    fireEvent.click(await screen.findByTestId('ads-unbind-s1'))
    fireEvent.click(screen.getByTestId('ads-unbind-purge'))
    fireEvent.click(screen.getByTestId('ads-unbind-confirm-button'))
    const note = await screen.findByTestId('ads-message')
    expect(note.textContent).toMatch(/Another machine/)
  })

})

describe('AmazonAdsPanel recheck', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // The authorize flow copies the link; give it a clipboard that works
    // so the fallback path never runs.
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    })
    mockApi.post.mockImplementation((url: string) => {
      if (url === '/api/ads/stores/sync')
        return Promise.resolve({ stores: [...AUTHORIZED, ...NOT_AUTHORIZED] })
      return Promise.resolve({ stores: NOT_AUTHORIZED, kept_shared: [] })
    })
  })

  /** Mint a consent link for a store and leave the panel waiting on the
   *  recheck click. */
  async function openAuthLink(
    storeId: string,
    openIn: 'browser' | 'ziniao' = 'browser',
  ) {
    mockApi.get.mockImplementation((url: string) => {
      if (url === `/api/ads/stores/${storeId}/auth-url`)
        return Promise.resolve({
          url: 'https://amazon.example/ap/oa?consent=1',
          expires_in: 1800,
          open_in: openIn,
        })
      return Promise.resolve({})
    })
    fireEvent.click(await screen.findByTestId(`ads-authorize-${storeId}`))
    await screen.findByTestId('ads-auth-link')
    // The link's copy step settles one animation frame later; only then
    // does the recheck button stop being busy.
    await waitFor(() =>
      expect(screen.getByTestId('ads-recheck')).not.toBeDisabled(),
    )
  }

  function syncAnswers(rows: unknown) {
    mockApi.post.mockImplementation((url: string) => {
      if (url === '/api/ads/stores/sync') return Promise.resolve({ stores: rows })
      return Promise.resolve({ stores: [], kept_shared: [] })
    })
  }

  /** `at` the service could have stamped on a consent made AFTER this
   *  link was minted (the click that mints happens later in real time). */
  const freshAt = () => new Date(Date.now() + 60_000).toISOString()
  const staleAt = () => new Date(Date.now() - 60_000).toISOString()

  it('says the account has no ad access when the consent bound no profiles', async () => {
    renderPanel()
    await openAuthLink('s2')
    syncAnswers([
      {
        ...NOT_AUTHORIZED[0],
        last_consent: { result: 'no_profiles', at: freshAt() },
      },
    ])
    fireEvent.click(screen.getByTestId('ads-recheck'))
    const note = await screen.findByTestId('ads-check-note')
    expect(note.textContent).toBe(enTranslation.ads.noAdPermission)
    // A failure is said as a failure, in the failure colour.
    expect(note.className).toContain('text-red-600')
  })

  it('says the service failed when the consent did not complete', async () => {
    renderPanel()
    await openAuthLink('s2')
    syncAnswers([
      {
        ...NOT_AUTHORIZED[0],
        last_consent: { result: 'failed', at: freshAt() },
      },
    ])
    fireEvent.click(screen.getByTestId('ads-recheck'))
    const note = await screen.findByTestId('ads-check-note')
    expect(note.textContent).toBe(enTranslation.ads.consentFailed)
    expect(note.className).toContain('text-red-600')
  })

  it('still asks to finish the consent when the service sends no field', async () => {
    renderPanel()
    await openAuthLink('s2')
    syncAnswers([NOT_AUTHORIZED[0]])
    fireEvent.click(screen.getByTestId('ads-recheck'))
    const note = await screen.findByTestId('ads-check-note')
    expect(note.textContent).toBe(enTranslation.ads.notYetAuthorized)
    // Not completed is also said in the failure colour.
    expect(note.className).toContain('text-red-600')
  })

  it('ignores a consent outcome older than the link it explains', async () => {
    renderPanel()
    await openAuthLink('s2')
    // A no_profiles from BEFORE this link was minted belongs to an
    // earlier attempt — the user has not even used this link yet.
    syncAnswers([
      {
        ...NOT_AUTHORIZED[0],
        last_consent: { result: 'no_profiles', at: staleAt() },
      },
    ])
    fireEvent.click(screen.getByTestId('ads-recheck'))
    const note = await screen.findByTestId('ads-check-note')
    expect(note.textContent).toBe(enTranslation.ads.notYetAuthorized)
  })

  it('links the docs on Ziniao blocking the consent page for a Ziniao store', async () => {
    renderPanel()
    await openAuthLink('s2', 'ziniao')
    // Ziniao refuses the consent page's domain until it is allowlisted;
    // the link section says so up front, before any recheck fails.
    const hint = screen.getByTestId('ads-ziniao-blocked')
    expect(hint.textContent).toContain(enTranslation.ads.ziniaoBlocked)
    const link = hint.querySelector('[data-testid="docs-help-link"]') as HTMLAnchorElement
    expect(link.href).toBe('https://docs.vibe-sellers.com/en/ads-api/#ziniao-blocked')
  })

  it('does not mention Ziniao for a store opened in a normal browser', async () => {
    renderPanel()
    await openAuthLink('s2', 'browser')
    expect(screen.queryByTestId('ads-ziniao-blocked')).toBeNull()
  })

  it('reports the bound marketplaces when the store is authorized', async () => {
    renderPanel()
    await openAuthLink('s1')
    syncAnswers(AUTHORIZED)
    fireEvent.click(screen.getByTestId('ads-recheck'))
    const message = await screen.findByTestId('ads-message')
    expect(message.textContent).toBe(
      enTranslation.ads.authorizedDone
        .replace('{{store}}', 'Shop A')
        .replace('{{markets}}', 'AE · SA'),
    )
    // The consent link section closes on success.
    await waitFor(() => expect(screen.queryByTestId('ads-auth-link')).toBeNull())
  })
})
