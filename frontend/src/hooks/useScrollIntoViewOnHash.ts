import { useEffect } from 'react'

/** Fired (detail: an element id) right after the app navigates to a
 *  page section — the router changes the URL without a hashchange. */
export const SCROLL_TO_EVENT = 'vibe:scroll-to'

/** Ask the page to bring element *id* into view (see the hook below). */
export function scrollToSection(id: string): void {
  window.dispatchEvent(new CustomEvent(SCROLL_TO_EVENT, { detail: id }))
}

/**
 * Bring the element with *id* into view when a link points at it: on
 * mount when the URL's hash names it (the page was opened at it — the
 * element may only render once its data loaded), and whenever
 * ``scrollToSection(id)`` is called while it is already on screen.
 */
export function useScrollIntoViewOnHash(id: string): void {
  useEffect(() => {
    const show = () => document.getElementById(id)?.scrollIntoView({ block: 'center' })
    if (window.location.hash === `#${id}`) show()
    const onEvent = (e: Event) => {
      if ((e as CustomEvent<string>).detail === id) show()
    }
    window.addEventListener(SCROLL_TO_EVENT, onEvent)
    return () => window.removeEventListener(SCROLL_TO_EVENT, onEvent)
  }, [id])
}
