import type { TFunction } from 'i18next'

import type { BrowserVendor } from '../types'

/** camelCase('client_not_running') → 'ClientNotRunning'. */
export const camelCase = (s: string) =>
  s.split('_').map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join('')

/**
 * The vendor-first text chain: the vendor's own override, else the
 * shared `browserVendor.<Suffix>` key with {{vendor}} interpolated.
 *
 * ONE copy of the chain — shared by the settings vendor panel (which
 * resolves its labels vendor-first) and the task error banner (which
 * speaks the store's vendor when the store uses one). Empty string
 * when neither key exists, so callers fall through to their own
 * generic text.
 */
export function vendorText(
  t: TFunction,
  vendor: BrowserVendor,
  displayName: string,
  suffix: string,
): string {
  const own = t(`${vendor.i18n_prefix}${suffix}`, {
    defaultValue: '',
    vendor: displayName,
  })
  if (own) return own
  return t(`browserVendor.${suffix}`, {
    defaultValue: '',
    vendor: displayName,
  })
}
