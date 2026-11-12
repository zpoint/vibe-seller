// First-run guide controller.
//
// "Seen" lives in this browser's localStorage under ONE key
// (`vibe-seller-guide-seen`) — not per user, not on the server. The
// key is written only when the user clicks Done / Skip / ×; until
// then a refresh shows the guide again. Accepted trade-offs: a
// private window, another browser or cleared site data shows it
// again; a shared browser shows it only to the first person.
// Replay via "?" never writes or clears the key. Every localStorage
// access is guarded: if storage throws, the guide still shows once
// per page load (in-memory guard) and never loops on re-render.
import { useCallback, useEffect, useRef, useState } from 'react'
import { sendEvent } from '../lib/telemetry'
import { FrontendEvent } from '../lib/telemetryEvents'
import type { AuthUser } from '../types'

export const GUIDE_STEP_COUNT = 3
export const GUIDE_SEEN_KEY = 'vibe-seller-guide-seen'

export type GuideCloseVia = 'done' | 'skip' | 'close'

export interface GuideController {
  open: boolean
  step: number
  next: () => void
  prev: () => void
  /** Leave the guide; writes the seen-key unless this was a replay. */
  skip: () => void
  /** Finish on the last step. */
  done: () => void
  /** The header X. */
  dismiss: () => void
  /** Reopen the guide without touching the key. */
  replay: () => void
}

/** true = seen, false = not, null = storage threw. */
function readSeen(): boolean | null {
  try {
    return !!localStorage.getItem(GUIDE_SEEN_KEY)
  } catch {
    return null
  }
}

function writeSeen(): void {
  try {
    localStorage.setItem(GUIDE_SEEN_KEY, new Date().toISOString())
  } catch {
    // Storage unavailable (private mode / quota / blocked): the
    // in-memory guard below still keeps this page load quiet.
  }
}

export function useOnboardingGuide(user: AuthUser | null): GuideController {
  const [open, setOpen] = useState(false)
  const [step, setStep] = useState(0)
  const replayRef = useRef(false)
  // Set when the auto-show fired while storage was throwing: show
  // once per page load, never loop.
  const shownAfterFailureRef = useRef(false)
  const userId = user?.id ?? null

  // Auto-show: signed in and no seen-key yet. The read happens
  // off-frame (rAF) so no setState runs synchronously in the effect
  // body; a re-render after dismissal re-reads the key and stays
  // quiet.
  useEffect(() => {
    if (!userId || open) return
    const raf = requestAnimationFrame(() => {
      const seen = readSeen()
      if (seen === true) return
      if (seen === null) {
        if (shownAfterFailureRef.current) return
        shownAfterFailureRef.current = true
      }
      replayRef.current = false
      setStep(0)
      setOpen(true)
    })
    return () => cancelAnimationFrame(raf)
  }, [userId, open])

  // "shown" fires on the closed->open edge only.
  const wasOpenRef = useRef(false)
  useEffect(() => {
    if (open && !wasOpenRef.current) {
      sendEvent(FrontendEvent.GUIDE_SHOWN, {
        trigger: replayRef.current ? 'replay' : 'auto',
      })
    }
    wasOpenRef.current = open
  }, [open])

  const close = useCallback((via: GuideCloseVia) => {
    sendEvent(FrontendEvent.GUIDE_CLOSED, { via, step: step + 1 })
    // Done / Skip / × all mark the guide as seen for this browser —
    // except a replay session, which must leave the key untouched.
    if (!replayRef.current) writeSeen()
    replayRef.current = false
    setOpen(false)
    setStep(0)
  }, [step])

  const skip = useCallback(() => close('skip'), [close])
  const done = useCallback(() => close('done'), [close])
  const dismiss = useCallback(() => close('close'), [close])
  const next = useCallback(() => {
    if (step + 1 >= GUIDE_STEP_COUNT) close('done')
    else setStep(step + 1)
  }, [step, close])
  const prev = useCallback(() => setStep(s => Math.max(0, s - 1)), [])
  const replay = useCallback(() => {
    replayRef.current = true
    setStep(0)
    setOpen(true)
  }, [])

  return { open, step, next, prev, skip, done, dismiss, replay }
}
