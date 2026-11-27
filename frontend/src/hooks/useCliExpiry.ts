import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { INTEGRATION_ATTENTION_EVENT } from './useIntegrationAttention'

/** Back from another tab re-checks, at most this often. */
const FOCUS_RECHECK_MS = 15_000

/**
 * Whether this enabled platform's authorization expired. The server
 * checks with read-only calls (GET /api/integrations/cli/{key}/expiry).
 * Re-checked when its card reloads and when the window gets focus back.
 */
export function useCliExpiry(cardKey: string, active: boolean, reloaded: unknown) {
  const [answer, setAnswer] = useState<{ expired: boolean | null } | null>(null)
  const [nudge, setNudge] = useState(0)
  const seq = useRef(0)
  const lastAt = useRef(0)

  useEffect(() => {
    if (!active) return
    const onFocus = () => {
      if (Date.now() - lastAt.current > FOCUS_RECHECK_MS) setNudge(n => n + 1)
    }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [active])

  useEffect(() => {
    if (!active) return
    const mine = ++seq.current
    lastAt.current = Date.now()
    api
      .get(`/api/integrations/cli/${cardKey}/expiry`)
      .then((r: { expired?: boolean | null } | null) => {
        if (mine !== seq.current) return
        setAnswer({ expired: r?.expired ?? null })
        window.dispatchEvent(new Event(INTEGRATION_ATTENTION_EVENT))
      })
      .catch(() => {
        // Keep the sign-in status; the next check retries.
        if (mine === seq.current) setAnswer(prev => prev ?? { expired: null })
      })
  }, [cardKey, active, reloaded, nudge])

  return {
    expired: active ? (answer?.expired ?? null) : null,
    loading: active && answer === null,
  }
}
