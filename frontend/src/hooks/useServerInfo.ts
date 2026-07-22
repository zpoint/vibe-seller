import { useState, useEffect } from 'react'
import type { ServerPlatform } from '../types'

// /api/system/info on mount: platform + version (top-left).
export function useServerInfo() {
  const [serverPlatform, setServerPlatform] = useState<ServerPlatform | null>(null)
  const [serverVersion, setServerVersion] = useState<string>('')
  useEffect(() => {
    fetch('/api/system/info', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(info => {
        if (info?.platform) setServerPlatform(info.platform)
        const v = String(info?.version || ''), c = String(info?.commit || '')
        const pick = v && !v.includes('+') && !v.includes('dev') ? v : (c || v)
        if (pick) setServerVersion(pick)
      })
      .catch(() => {})
  }, [])
  return { serverPlatform, serverVersion }
}
