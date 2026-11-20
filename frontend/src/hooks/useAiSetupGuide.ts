// When the "click here to add a model" highlight on Settings → AI shows.
//
// Opens when:
//   - the set-up-AI dialog sent the user here (aiSetupStore request), or
//   - first visit to this tab while AI isn't set up and this browser
//     has not dismissed it (`vibe-seller-ai-guide-seen`, same guarded
//     storage as the first-run guide).
// Never opens when AI is set up. Closes for good (this browser) on
// "Got it" or once the user clicks the highlighted button.
//
// Readiness is re-asked whenever the user's default model or the list
// of models changes — the two things a setup on this page changes.
import { useCallback, useEffect, useRef, useState } from 'react'
import { aiSetupStore, fetchAiReadiness } from '../lib/aiSetup'
import { readSeen, writeSeen } from '../lib/guideStorage'
import { sendEvent } from '../lib/telemetry'
import { FrontendEvent } from '../lib/telemetryEvents'

export const AI_GUIDE_SEEN_KEY = 'vibe-seller-ai-guide-seen'

export type AiGuideCloseVia = 'later' | 'add_model'

export interface AiGuideController {
  open: boolean
  close: (via: AiGuideCloseVia) => void
  docsOpened: () => void
}

export function useAiSetupGuide(
  defaultProfileId: string | undefined,
  profileIds: string,
): AiGuideController {
  const [open, setOpen] = useState(false)
  const openRef = useRef(false)
  // Shown on this page: report once when AI then becomes set up.
  const shownRef = useRef<'prompt' | 'first_visit' | null>(null)
  // Storage threw: show at most once per page load, never loop.
  const shownAfterFailureRef = useRef(false)

  const show = useCallback((v: boolean) => {
    openRef.current = v
    setOpen(v)
  }, [])

  useEffect(() => {
    let alive = true
    void fetchAiReadiness().then(r => {
      if (!alive) return
      const fromPrompt = aiSetupStore.takeGuideRequest()
      if (r?.ready) {
        if (shownRef.current) {
          sendEvent(FrontendEvent.AI_SETUP_COMPLETED, { from_prompt: shownRef.current === 'prompt' })
          shownRef.current = null
        }
        if (openRef.current) show(false)
        return
      }
      if (openRef.current) return
      let trigger: 'prompt' | 'first_visit' | null = null
      if (fromPrompt) trigger = 'prompt'
      else if (r) {
        const seen = readSeen(AI_GUIDE_SEEN_KEY)
        if (seen === false) trigger = 'first_visit'
        else if (seen === null && !shownAfterFailureRef.current) {
          shownAfterFailureRef.current = true
          trigger = 'first_visit'
        }
      }
      if (!trigger) return
      shownRef.current = trigger
      sendEvent(FrontendEvent.AI_SETUP_GUIDE_SHOWN, { trigger })
      show(true)
    })
    return () => { alive = false }
  }, [defaultProfileId, profileIds, show])

  const close = useCallback((via: AiGuideCloseVia) => {
    sendEvent(FrontendEvent.AI_SETUP_GUIDE_CLOSED, { via })
    writeSeen(AI_GUIDE_SEEN_KEY)
    show(false)
  }, [show])

  const docsOpened = useCallback(() => {
    sendEvent(FrontendEvent.AI_SETUP_GUIDE_DOCS_OPENED, {})
  }, [])

  return { open, close, docsOpened }
}
