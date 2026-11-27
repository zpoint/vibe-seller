/**
 * The option label of one store/env row in the generic vendor profile
 * picker (VendorSection). ONE rule, decided ONLY from the row's own
 * fields (ref vs name — never the vendor key), in two shapes:
 *
 * - A row LISTED BY NAME (ref empty, or ref equals `name` — 悦互联 /
 *   稳卖): STORE NAME FIRST — `{name} · {remote_env} · {platform}`,
 *   e.g. `示例甲1店 · #4 美国站 (分组) · www.amazon.com`.
 * - A row with an OPAQUE ref (ref non-empty and differs from `name` —
 *   AdsPower / BitBrowser): `{display} · {platform} · {ref}` with
 *   display = remote_env || name, e.g.
 *   `#4 美国站 (分组) · www.amazon.com · p0000001`.
 *
 * In both shapes the value the row BINDS by is visible (the name, or
 * the ref), empty parts are dropped with no dangling separators, and a
 * part exactly equal to an earlier one is dropped so nothing is
 * duplicated. An unavailable row's already-translated reason is
 * appended after an em dash, exactly as the picker always appended it.
 * What a row binds by (bindValue) does NOT change — only its label.
 *
 * Pure on purpose: one place holds the rule, and tests pin every shape.
 */
import type { VendorStoreRef } from '../types'

export function vendorStoreOptionLabel(
  s: Pick<VendorStoreRef, 'name' | 'platform' | 'remote_env' | 'ref'>,
  unavailableReason = '',
): string {
  const ref = s.ref || ''
  const nameListed = !ref || ref === s.name
  const parts = nameListed
    ? [s.name, s.remote_env, s.platform]
    : [s.remote_env || s.name, s.platform, ref]
  const kept: string[] = []
  for (const part of parts) {
    if (part && !kept.includes(part)) kept.push(part)
  }
  const label = kept.join(' · ')
  return unavailableReason ? `${label} — ${unavailableReason}` : label
}
