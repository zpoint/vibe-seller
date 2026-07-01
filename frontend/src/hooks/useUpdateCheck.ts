import { useEffect, useState } from 'react'
import type { UpdateCheckResult } from '../types'

const DISMISSED_KEY = 'vibe-seller:update-dismissed-version'

/**
 * Update-available popup: checked once per login, not gated on how
 * long the server's been up — this is purely a frontend page-load
 * behavior. Suppressed for the rest of the browser session once
 * dismissed for a given latest_version via sessionStorage, so a
 * refresh or new tab in the same session doesn't re-nag; closing and
 * reopening the browser clears sessionStorage and checks again.
 */
export function useUpdateCheck(currentUser: unknown) {
  const [updateCheck, setUpdateCheck] = useState<UpdateCheckResult | null>(null)

  useEffect(() => {
    if (!currentUser) return
    fetch('/api/system/update-check', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then((result: UpdateCheckResult | null) => {
        if (!result || result.dev || !result.update_available) return
        if (sessionStorage.getItem(DISMISSED_KEY) === result.latest_version) return
        setUpdateCheck(result)
      })
      .catch(() => {})
  }, [currentUser])

  const dismissUpdateCheck = () => {
    if (updateCheck?.latest_version) sessionStorage.setItem(DISMISSED_KEY, updateCheck.latest_version)
    setUpdateCheck(null)
  }

  return { updateCheck, dismissUpdateCheck }
}
