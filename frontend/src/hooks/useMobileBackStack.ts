import { useEffect, useRef } from 'react'

interface Args {
  isMobile: boolean
  navOpen: boolean
  closeNav: () => void
}

/**
 * Make the device/browser Back button close the mobile nav **drawer**
 * instead of leaving the site.
 *
 * Task / schedule detail are now real routes (`/tasks/$id`,
 * `/schedules/$id`), so the router's own history already makes Back
 * close them — this hook no longer touches them. Only the transient nav
 * drawer isn't a route, so it keeps a single dedicated history entry:
 * push one when the drawer opens, and on `popstate` close the drawer.
 * The pushed entry keeps the SAME URL, so the router (which also listens
 * to popstate) just re-syncs to the unchanged location — no route
 * change, no conflict. No-op on desktop.
 */
export function useMobileBackStack({ isMobile, navOpen, closeNav }: Args) {
  const pushedRef = useRef(false)

  useEffect(() => {
    if (!isMobile) return
    if (navOpen && !pushedRef.current) {
      pushedRef.current = true
      window.history.pushState({ vsDrawer: true }, '')
    } else if (!navOpen && pushedRef.current) {
      pushedRef.current = false
      window.history.back()
    }
  }, [isMobile, navOpen])

  useEffect(() => {
    const onPop = () => {
      if (!pushedRef.current) return
      pushedRef.current = false
      closeNav()
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [closeNav])
}
