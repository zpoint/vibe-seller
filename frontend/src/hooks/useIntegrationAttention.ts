import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

/** Fired when something may have changed which platforms need signing
 *  in again (a permission check finished, a sign-in ended). */
export const INTEGRATION_ATTENTION_EVENT = 'vibe:integration-attention'

/** How often the bar re-asks on its own (the server re-checks the
 *  platforms at most every 30 minutes anyway). */
const RECHECK_MS = 10 * 60_000

export interface IntegrationAttentionItem {
  key: string
  title: string
}

/**
 * Enabled workplace platforms whose sign-in, or one of whose
 * capabilities, expired (GET /api/integrations/cli/attention — empty for
 * everyone but admins, the only ones who can sign them in again).
 * Re-asked on mount, every few minutes, when the window gets focus back
 * and on INTEGRATION_ATTENTION_EVENT. State only changes when the
 * content does.
 */
export function useIntegrationAttention(): IntegrationAttentionItem[] {
  const [items, setItems] = useState<IntegrationAttentionItem[]>([])
  const [nudge, setNudge] = useState(0)
  const seq = useRef(0)
  const last = useRef('[]')

  useEffect(() => {
    const bump = () => setNudge(n => n + 1)
    window.addEventListener(INTEGRATION_ATTENTION_EVENT, bump)
    window.addEventListener('focus', bump)
    const timer = setInterval(bump, RECHECK_MS)
    return () => {
      window.removeEventListener(INTEGRATION_ATTENTION_EVENT, bump)
      window.removeEventListener('focus', bump)
      clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    const mine = ++seq.current
    api
      .get('/api/integrations/cli/attention')
      .then((r: { items?: IntegrationAttentionItem[] } | null) => {
        if (mine !== seq.current) return
        const next = r?.items ?? []
        const key = JSON.stringify(next)
        if (key === last.current) return
        last.current = key
        setItems(next)
      })
      .catch(() => {
        /* non-fatal — the next nudge retries */
      })
  }, [nudge])

  return items
}
