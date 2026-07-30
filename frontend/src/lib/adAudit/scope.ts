// What the reviewer has chosen to ACT ON.
//
// A report can cover one marketplace or a dozen, and a reviewer rarely wants
// to execute all of it at once: a first pass is usually "just this campaign,
// just this market, leave the rest for next week". So the submitted decision
// set is not "everything I looked at" — it is what survived the scope.
//
// Modelled as EXCLUSIONS rather than inclusions so the default is "all in".
// A newly-drilled market appearing in a later report is then in scope
// automatically; an inclusion model would silently drop it because nobody
// had ticked it.
//
// The three levels nest: dropping a country drops its platforms and their
// campaigns. A campaign is in scope only if nothing above it was dropped.

import type { AuditCampaign } from './types'

export type ScopeKey = string

export const countryKey = (country: string): ScopeKey => `country:${country}`
export const platformKey = (country: string, platform: string): ScopeKey =>
  `platform:${country}/${platform}`
export const campaignKey = (id: string): ScopeKey => `campaign:${id}`

/** Exclusions, by scope key. Empty means the whole report is in scope. */
export type ExcludedScopes = ReadonlySet<ScopeKey>

export const NOTHING_EXCLUDED: ExcludedScopes = new Set<ScopeKey>()

/** True when this campaign survives every level of the scope. */
export function campaignInScope(
  c: AuditCampaign,
  excluded: ExcludedScopes,
): boolean {
  return (
    !excluded.has(countryKey(c.country)) &&
    !excluded.has(platformKey(c.country, c.platform)) &&
    !excluded.has(campaignKey(c.id))
  )
}

/** Toggle one scope key, returning a new set (never mutates). */
export function toggleScope(
  excluded: ExcludedScopes,
  key: ScopeKey,
): ExcludedScopes {
  const next = new Set(excluded)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  return next
}

/**
 * Drop everything except one campaign — the "keep only this one" shortcut.
 *
 * Expressed as campaign-level exclusions only, deliberately: excluding the
 * other countries instead would then swallow a campaign the reviewer later
 * re-enables inside one of them.
 */
export function keepOnly(
  campaigns: readonly AuditCampaign[],
  id: string,
): ExcludedScopes {
  return new Set(campaigns.filter((c) => c.id !== id).map((c) => campaignKey(c.id)))
}

export interface ScopeCounts {
  countries: number
  platforms: number
  campaigns: number
  /** Campaigns dropped by any level. */
  droppedCampaigns: number
}

export function scopeCounts(
  campaigns: readonly AuditCampaign[],
  excluded: ExcludedScopes,
): ScopeCounts {
  const inScope = campaigns.filter((c) => campaignInScope(c, excluded))
  return {
    countries: new Set(inScope.map((c) => c.country)).size,
    platforms: new Set(inScope.map((c) => `${c.country}/${c.platform}`)).size,
    campaigns: inScope.length,
    droppedCampaigns: campaigns.length - inScope.length,
  }
}

/** Every (country, platform) pair present in the report, in report order. */
export function platformsOf(
  campaigns: readonly AuditCampaign[],
): { country: string; platform: string }[] {
  const seen = new Set<string>()
  const out: { country: string; platform: string }[] = []
  for (const c of campaigns) {
    const k = `${c.country}/${c.platform}`
    if (seen.has(k)) continue
    seen.add(k)
    out.push({ country: c.country, platform: c.platform })
  }
  return out
}
