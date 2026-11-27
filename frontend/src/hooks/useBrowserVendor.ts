import { useEffect, useState } from 'react'

import { api } from '../api'
import { useBrowserVendors } from './useBrowserVendors'
import type { VendorStoreRef } from '../types'

/**
 * One vendor's store-dropdown state for the create-store form, driven by
 * the generic vendor routes: the dropdown lists the stores the vendor's
 * logged-in client can see via GET /api/browser-vendors/{key}/stores,
 * decoding the backend's structured status into a `<key>:<status>` error
 * string.
 *
 * The key changing (the user picks another backend) resets the state —
 * each vendor's section fetches its own list on mount.
 */
export function useBrowserVendor(key: string) {
  const [stores, setStores] = useState<VendorStoreRef[]>([])
  const [error, setError] = useState('')
  const [fetching, setFetching] = useState(false)

  useEffect(() => {
    setStores([])
    setError('')
    setFetching(false)
  }, [key])

  const fetchStores = async () => {
    setFetching(true)
    setStores([])
    setError('')
    try {
      const list = (await api.get(
        `/api/browser-vendors/${key}/stores`,
      )) as VendorStoreRef[]
      setStores(list)
      if (list.length === 0) setError(`${key}:no_stores`)
    } catch (e) {
      const msg = e instanceof Error ? e.message : ''
      let status = 'connect_error'
      try {
        const d = JSON.parse(msg)
        if (d?.status) status = d.status
      } catch {
        /* non-JSON error message — keep connect_error */
      }
      setError(`${key}:${status}`)
    } finally {
      setFetching(false)
    }
  }

  const reset = () => {
    setStores([])
    setError('')
  }

  return { stores, error, fetching, fetchStores, reset }
}

/**
 * Re-export so components that need both the vendor list and one
 * vendor's stores can import from one place.
 */
export { useBrowserVendors }
