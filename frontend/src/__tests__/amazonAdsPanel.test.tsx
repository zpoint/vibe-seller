/**
 * AmazonAdsPanel: no key to enter, every store listed, and revoking an
 * authorization asks first (with an opt-in to delete the history).
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

  it('says it is Amazon only', async () => {
    renderPanel()
    await screen.findByTestId('ads-stores')
    expect(screen.getByText(/Amazon stores only/)).toBeTruthy()
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
