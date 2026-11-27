import type { TFunction } from 'i18next'

import type { BrowserVendor } from '../types'

/** camelCase('client_not_running') → 'ClientNotRunning'. */
export const camelCase = (s: string) =>
  s.split('_').map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join('')

/**
 * The vendor-first text chain: the vendor's own override, else the
 * shared `browserVendor.*` key with {{vendor}} interpolated.
 *
 * ONE copy of the chain — shared by the settings vendor panel (which
 * resolves its labels vendor-first) and the task error banner (which
 * speaks the store's vendor when the store uses one). Empty string
 * when neither key exists, so callers fall through to their own
 * generic text.
 *
 * *Context* picks the namespace — the same chain, two wordings:
 * - 'form' (the store form): `${prefix}<Suffix>` / `browserVendor.<Suffix>`.
 *   Form sentences may name form buttons (「刷新」).
 * - 'task' (a failed task's banner): `${prefix}task.<Suffix>` /
 *   `browserVendor.task.<Suffix>`. Task sentences may only name what
 *   the task view offers (「重试」) — reusing a form sentence there
 *   tells the user to click a button that is not on the screen.
 */
export function vendorText(
  t: TFunction,
  vendor: BrowserVendor,
  displayName: string,
  suffix: string,
  context: 'form' | 'task' = 'form',
): string {
  const scope = context === 'task' ? `task.${suffix}` : suffix
  const own = t(`${vendor.i18n_prefix}${scope}`, {
    defaultValue: '',
    vendor: displayName,
  })
  if (own) return own
  return t(`browserVendor.${scope}`, {
    defaultValue: '',
    vendor: displayName,
  })
}
