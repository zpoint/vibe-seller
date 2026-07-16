import { useEffect, useRef } from 'react'
import { api } from '../api'

// Poll cadence. On each tick we either roll the session forward (if the
// user has interacted since the last roll) or just probe liveness. 60s
// bounds how long an idle-expired session can sit on a frozen screen
// before it's bounced to login.
const TICK_MS = 60_000

/**
 * Keeps an interactive login session honest while the app is open.
 *
 * Two jobs, both leaning on the shared API client (which fires
 * `AUTH_EXPIRED_EVENT` on any 401 → App.tsx clears the user → LoginPage):
 *
 *  1. **Roll on activity** — when the user has done something since the
 *     last roll, POST /api/auth/refresh to push the 24h window forward.
 *     An actively-used session therefore never expires under the user.
 *  2. **Detect idle expiry** — when there's been no activity, GET
 *     /api/auth/me as a heartbeat. Once the idle window lapses this 401s
 *     and the client redirects to login instead of hanging.
 *
 * Refreshing is gated on *real* interaction (not the heartbeat itself) —
 * otherwise the passive poll would keep the session alive forever and
 * "idle > 24h logs out" could never happen.
 *
 * Only run when auth is actually required; with auth disabled the server
 * never 401s, so there's nothing to keep alive or detect.
 */
export function useSessionKeepalive(enabled: boolean) {
  // Flipped true by any real interaction, cleared each time we roll the
  // session. A flag (not a timestamp diff) so behavior never depends on
  // clock granularity — one interaction between ticks rolls once.
  const activitySinceRefresh = useRef(false)

  useEffect(() => {
    if (!enabled) return

    const bump = () => { activitySinceRefresh.current = true }
    const events = ['pointerdown', 'keydown', 'visibilitychange'] as const
    events.forEach(e => window.addEventListener(e, bump, { passive: true }))

    const tick = () => {
      if (activitySinceRefresh.current) {
        activitySinceRefresh.current = false
        api.post('/api/auth/refresh').catch(() => { /* 401 → auth:expired */ })
      } else {
        api.get('/api/auth/me').catch(() => { /* 401 → auth:expired */ })
      }
    }

    const id = window.setInterval(tick, TICK_MS)
    return () => {
      window.clearInterval(id)
      events.forEach(e => window.removeEventListener(e, bump))
    }
  }, [enabled])
}
