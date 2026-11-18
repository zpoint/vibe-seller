import { useState } from 'react'

import { api } from '../api'
import type { YuehulianStore } from '../types'

/**
 * Yuehulian store-dropdown state for the create-store form. No account
 * (the user logs into the GUI client manually); the dropdown lists the
 * stores that login can see via GET /api/yuehulian/stores, decoding the
 * backend's structured status into a `yuehulian:<status>` error string.
 */
export function useYuehulian() {
  const [yuehulianStores, setYuehulianStores] = useState<YuehulianStore[]>([])
  const [yuehulianError, setYuehulianError] = useState('')
  const [fetchingYuehulian, setFetchingYuehulian] = useState(false)

  const fetchYuehulianStores = async () => {
    setFetchingYuehulian(true)
    setYuehulianStores([])
    setYuehulianError('')
    try {
      const stores: YuehulianStore[] = await api.get('/api/yuehulian/stores')
      setYuehulianStores(stores)
      if (stores.length === 0) setYuehulianError('yuehulian:no_stores')
    } catch (e) {
      const msg = e instanceof Error ? e.message : ''
      let status = 'connect_error'
      try {
        const d = JSON.parse(msg)
        if (d?.status) status = d.status
      } catch {
        /* non-JSON error message — keep connect_error */
      }
      setYuehulianError('yuehulian:' + status)
    } finally {
      setFetchingYuehulian(false)
    }
  }

  const resetYuehulian = () => {
    setYuehulianStores([])
    setYuehulianError('')
  }

  return {
    yuehulianStores,
    yuehulianError,
    fetchingYuehulian,
    fetchYuehulianStores,
    resetYuehulian,
  }
}
