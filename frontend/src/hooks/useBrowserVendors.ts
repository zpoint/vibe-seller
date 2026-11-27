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

export function useBrowserVendors() {
  const [vendors, setVendors] = useState<BrowserVendor[] | null>(null)
  useEffect(() => {
    let alive = true
    void fetchVendors().then((v) => {
      if (alive) setVendors(v)
    })
    return () => {
      alive = false
    }
  }, [])
  return vendors
}
