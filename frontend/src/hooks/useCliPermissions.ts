import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { INTEGRATION_ATTENTION_EVENT } from './useIntegrationAttention'

/** One capability a platform grants (and expires) on its own — WeCom
 *  grants each for 7 days (app/cli_integrations/wecom.py). */
export interface CliPermission {
  key: string
  state: 'ok' | 'expired' | 'unknown'
  renew_url: string | null
}

/** Back from another tab (renewing in WeCom) re-checks, at most this often. */
const FOCUS_RECHECK_MS = 15_000

/**
 * Each capability's state (GET /api/integrations/cli/{key}/permissions —
 * one read-only call apiece, so it takes a second or two). Checked when
 * the row shows a signed-in per-permission platform, again whenever the
 * row's card is reloaded (a sign-in finished), and when the window gets
 * focus back. Every answer nudges the bar at the top of the app.
 *
 * ``loading`` holds only until the first answer; a re-check keeps
 * showing the last one.
 */
export function useCliPermissions(cardKey: string, active: boolean, reloaded: unknown) {
  const [answer, setAnswer] = useState<{ items: CliPermission[] | null } | null>(null)
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
      .get(`/api/integrations/cli/${cardKey}/permissions`)
      .then((r: { permissions?: CliPermission[] | null } | null) => {
        if (mine !== seq.current) return
        setAnswer({ items: r?.permissions ?? null })
        window.dispatchEvent(new Event(INTEGRATION_ATTENTION_EVENT))
      })
      .catch(() => {
        // The row keeps its sign-in status; the next check retries.
        if (mine === seq.current) setAnswer(prev => prev ?? { items: null })
      })
  }, [cardKey, active, reloaded, nudge])

  const items = active ? (answer?.items ?? null) : null
  return {
    items,
    loading: active && answer === null,
    anyExpired: (items ?? []).some(p => p.state === 'expired'),
  }
}
