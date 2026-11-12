/**
 * The bare landing URL '/': App feeds it the store-list fetch and it
 * jumps to the first store, or to /tasks when there are none — so the
 * homepage opens ON a store instead of the All Stores list, which
 * misleads users into creating store-less tasks.
 *
 * The jump fires only if the LIVE URL is still exactly '/' at the
 * moment the list arrives — read from window.location at that instant,
 * never from a value captured at render time. Any click (All Stores, a
 * store, Settings) or deep link has already moved the URL off '/', so
 * the jump becomes a no-op and can never override a route the user or
 * a link chose. That is what makes this race-free where the old
 * (pre-#94) jump was not: it guarded on a `userActedRef` flag set by
 * the click handlers and reset by the login effect, so an explicit nav
 * made during login was invisible to it and the late jump bounced the
 * user back to a store (the e2e race that got the jump removed).
 */
import type { Store } from '../types'

export function resolveLanding(
  stores: Promise<Store[]>,
  jump: (first: Store | null) => void,
): void {
  const guarded = (list: Store[]) => {
    if (window.location.pathname === '/') jump(list[0] ?? null)
  }
  stores.then(guarded).catch(() => guarded([]))
}
