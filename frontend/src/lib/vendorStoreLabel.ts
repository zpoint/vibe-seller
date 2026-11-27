/**
 * The option label of one store/env row in the generic vendor profile
 * picker (VendorSection): the human name FIRST, then the platform, then
 * the ref — `{display} · {platform} · {ref}`, e.g.
 * `#4 美国站 (分组) · www.amazon.com · p0000001`.
 *
 * `display` is the row's `remote_env` when the vendor reports one (the
 * name the seller knows the env by), else `name`. The third part is the
 * value the row BINDS by — `ref`, else `name` — so a vendor that lists
 * by name (悦互联 / 稳卖) keeps its store name even when `remote_env`
 * heads the label. A part is dropped only when it is EMPTY or EXACTLY
 * EQUAL to an earlier part (the ref is deduped against the display,
 * never against `name`), so nothing is duplicated and no separator
 * dangles. An unavailable row's already-translated reason is appended
 * after an em dash, exactly as the picker always appended it.
 *
 * Pure on purpose: one place holds the rule, and tests pin every shape.
 */
import type { VendorStoreRef } from '../types'

export function vendorStoreOptionLabel(
  s: Pick<VendorStoreRef, 'name' | 'platform' | 'remote_env' | 'ref'>,
  unavailableReason = '',
): string {
  const display = s.remote_env || s.name
  const bind = s.ref || s.name
  const parts: string[] = []
  for (const part of [display, s.platform, bind]) {
    if (part && !parts.includes(part)) parts.push(part)
  }
  const label = parts.join(' · ')
  return unavailableReason ? `${label} — ${unavailableReason}` : label
}
