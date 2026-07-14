import { useState, useEffect } from 'react'

// Single source of truth for the mobile breakpoint. Matches Tailwind's
// `md` (>=768px = desktop three-pane; <768px = mobile drill-down stack).
const MOBILE_QUERY = '(max-width: 767px)'

/**
 * True when the viewport is phone-width. Drives the layout switch
 * between the desktop three-pane view and the mobile navigation stack.
 * Structural (which pane is mounted) decisions use this; purely visual
 * tweaks should prefer Tailwind `md:` classes so they stay in CSS.
 */
export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(MOBILE_QUERY).matches,
  )

  useEffect(() => {
    const mq = window.matchMedia(MOBILE_QUERY)
    const onChange = () => setIsMobile(mq.matches)
    mq.addEventListener('change', onChange)
    // Sync once in case the query changed between render and effect.
    onChange()
    return () => mq.removeEventListener('change', onChange)
  }, [])

  return isMobile
}
