import { useEffect, useState } from 'react'

import { api } from '../api'
import type { BrowserVendor } from '../types'

/**
 * Every registered browser vendor's metadata (GET /api/browser-vendors),
 * in registration order. The store form's backend options, credential
 * fields and profile pickers all render from this — one fetch, shared
 * across every consumer via a module-level cache.
 */

let cache: Promise<BrowserVendor[]> | null = null

function fetchVendors(): Promise<BrowserVendor[]> {
  if (!cache) {
    cache = api
      .get('/api/browser-vendors')
      .then((v) => v as BrowserVendor[])
      .catch(() => {
        // A failed fetch must not pin a rejected promise in the cache.
        cache = null
        return []
      })
  }
  return cache
}

export function resetBrowserVendorsCacheForTests(): void {
  // App tests boot several independent backends in one process. Do not let
  // an earlier fixture's successful registry response leak into the next.
  cache = null
}

export function useBrowserVendors(authKey?: string | null) {
  const [vendors, setVendors] = useState<BrowserVendor[] | null>(null)
  useEffect(() => {
    let alive = true
    void fetchVendors().then((v) => {
      if (alive) setVendors(v)
    })
    return () => {
      alive = false
    }
    // An anonymous 401 resolves to [] and clears the promise cache. The
    // caller's auth identity changing (notably login) must start a fresh
    // fetch, or the backend picker stays empty until a full page reload.
  }, [authKey])
  return vendors
}
