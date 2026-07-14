import { useEffect, useRef } from 'react'

interface Args {
  isMobile: boolean
  navOpen: boolean
  hasTask: boolean
  hasSchedule: boolean
  closeNav: () => void
  closeTask: () => void
  closeSchedule: () => void
}

/**
 * Make the device/browser Back button pop the mobile drill-down stack
 * (nav drawer → task list → task detail) instead of leaving the site.
 *
 * We add exactly one history entry whenever a "sub-screen" is open and
 * pop the top-most level on `popstate`, so Back means "up one level".
 * Closing via in-app UI (the ← bar / scrim) drops our entry so a later
 * Back still leaves the app as the user expects. No-op on desktop.
 */
export function useMobileBackStack({
  isMobile, navOpen, hasTask, hasSchedule, closeNav, closeTask, closeSchedule,
}: Args) {
  const subOpen = isMobile && (navOpen || hasTask || hasSchedule)
  const pushedRef = useRef(false)

  useEffect(() => {
    if (!isMobile) return
    if (subOpen && !pushedRef.current) {
      pushedRef.current = true
      window.history.pushState({ vsSub: true }, '')
    } else if (!subOpen && pushedRef.current) {
      pushedRef.current = false
      window.history.back()
    }
  }, [isMobile, subOpen])

  useEffect(() => {
    const onPop = () => {
      if (!pushedRef.current) return
      pushedRef.current = false
      // Close only the top-most level. If a lower level is still open the
      // push effect re-adds an entry for it, so each Back peels one.
      if (navOpen) closeNav()
      else if (hasTask) closeTask()
      else if (hasSchedule) closeSchedule()
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [navOpen, hasTask, hasSchedule, closeNav, closeTask, closeSchedule])
}
