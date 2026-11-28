/** Submit the sidebar's create-store form with at-most-once feedback. */
import { storeCreateError } from '../api'
import { findStoreNameCollision } from '../lib/storeNameCollision'
import type { BrowserVendor, Store } from '../types'

export interface CreateStoreDeps {
  api: { post(url: string, body: unknown): Promise<unknown> }
  stores: Store[]
  newStoreName: string
  newStoreBackend: string
  genericVendor: BrowserVendor | null
  selectedZiniaoAccountId: string
  selectedBrowserOauth: string
  newStoreProxyServer: string
  newStoreProxyBypass: string
  createStoreInFlight: { current: boolean }
  setCreatingStore: (value: boolean) => void
  setCreateStoreError: (value: string) => void
  t: (key: string, options?: Record<string, unknown>) => string
  /** Clear the form and close it after a successful POST. */
  onCreated: () => void
  loadStores: () => Promise<unknown>
}

export async function createStore(deps: CreateStoreDeps): Promise<void> {
  const d = deps
  if (d.createStoreInFlight.current || !d.newStoreName.trim()) return
  d.setCreateStoreError('')
  const collision = findStoreNameCollision(d.newStoreName, d.stores)
  if (collision) {
    d.setCreateStoreError(d.t('settings.storeNameCollision', {
      name: collision.store.name, slug: collision.slug,
    }))
    return
  }
  if (d.newStoreBackend === 'ziniao' && (!d.selectedZiniaoAccountId || !d.selectedBrowserOauth)) {
    d.setCreateStoreError(d.t('settings.ziniaoSelectProfile'))
    return
  }
  if (d.genericVendor && !d.selectedBrowserOauth) {
    d.setCreateStoreError(d.t(`${d.genericVendor.i18n_prefix}SelectStore`))
    return
  }
  const body: Record<string, unknown> = {
    name: d.newStoreName,
    browser_backend: d.newStoreBackend,
  }
  if (d.newStoreBackend === 'ziniao') {
    body.ziniao_account_id = d.selectedZiniaoAccountId
    body.browser_oauth = d.selectedBrowserOauth
  }
  if (d.genericVendor) body.browser_oauth = d.selectedBrowserOauth
  if (d.newStoreBackend === 'chrome' && (d.newStoreProxyServer.trim() || d.newStoreProxyBypass.trim())) {
    const bc: Record<string, string> = {}
    if (d.newStoreProxyServer.trim()) bc.proxy_server = d.newStoreProxyServer.trim()
    if (d.newStoreProxyBypass.trim()) bc.proxy_bypass = d.newStoreProxyBypass.trim()
    body.browser_config = bc
  }

  // The ref closes the same-tick gap before React disables the button.
  d.createStoreInFlight.current = true
  d.setCreatingStore(true)
  try { await d.api.post('/api/stores', body) }
  catch (e) { d.setCreateStoreError(storeCreateError(e, d.t)); return }
  finally { d.createStoreInFlight.current = false; d.setCreatingStore(false) }
  d.onCreated()
  await d.loadStores()
}
