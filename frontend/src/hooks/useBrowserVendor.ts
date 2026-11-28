import { useEffect, useRef, useState } from 'react'

import { api } from '../api'
import { useBrowserVendors } from './useBrowserVendors'
import type { VendorStoreRef } from '../types'

/**
 * One vendor's store-dropdown state for the create-store form, driven by
 * the generic vendor routes: the dropdown lists the stores the vendor's
 * logged-in client can see via GET /api/browser-vendors/{key}/stores,
 * decoding the backend's structured status into a `<key>:<status>` error
 * string, with the backend's `message` kept alongside (the last resort
 * of the status-text fallback chain).
 *
 * The key changing (the user picks another backend) resets the state —
 * each vendor's section fetches its own list on mount.
 */
export function useBrowserVendor(key: string) {
  const [stores, setStores] = useState<VendorStoreRef[]>([])
  const [error, setError] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [fetching, setFetching] = useState(false)
  const generation = useRef(0)
  const currentKey = useRef(key)
  if (currentKey.current !== key) {
    // Invalidate an old vendor's in-flight list during render, before any
    // child effect can start the new vendor's fetch.
    currentKey.current = key
    generation.current += 1
  }

  useEffect(() => {
    setStores([])
    setError('')
    setErrorMessage('')
    setFetching(false)
  }, [key])

  const fetchStores = async () => {
    const request = ++generation.current
    setFetching(true)
    setStores([])
    setError('')
    setErrorMessage('')
    try {
      const list = (await api.get(
        `/api/browser-vendors/${key}/stores`,
      )) as VendorStoreRef[]
      if (request !== generation.current) return
      setStores(list)
      if (list.length === 0) setError(`${key}:no_stores`)
    } catch (e) {
      if (request !== generation.current) return
      const msg = e instanceof Error ? e.message : ''
      let status = 'connect_error'
      let message = ''
      try {
        const d = JSON.parse(msg)
        if (d?.status) status = d.status
        if (typeof d?.message === 'string') message = d.message
      } catch {
        /* non-JSON error message — keep connect_error */
      }
      setError(`${key}:${status}`)
      setErrorMessage(message)
    } finally {
      if (request === generation.current) setFetching(false)
    }
  }

  const reset = () => {
    generation.current += 1
    setStores([])
    setError('')
    setErrorMessage('')
    setFetching(false)
  }

  return { stores, error, errorMessage, fetching, fetchStores, reset }
}

/**
 * Re-export so components that need both the vendor list and one
 * vendor's stores can import from one place.
 */
export { useBrowserVendors }
