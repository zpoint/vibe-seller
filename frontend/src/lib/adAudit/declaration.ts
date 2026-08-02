// Which result gets a review console, and how much of it may be acted on.
//
// This used to be `looksLikeAuditReport(text)` — a regex over the agent's
// own markdown looking for a `## <platform> <CC>` heading and a
// `drilled N/N` line. Two conditions, both satisfied by prose the agent
// chose to write, neither checking what the task was for. A task asked to
// create two campaigns for one product tripped it and was handed a
// console offering 51 campaigns of four-day-old numbers as bid decisions.
//
// The server now records what each phase declared before doing the work
// (`AD_TASK.json` / `/api/tasks/:id/ad-declarations`). The console asks
// that instead: does this phase say it is an audit, and which campaigns
// did it say it was about.

import type { AuditCampaign, AuditDoc } from './types'

export type AdTaskKind = 'audit' | 'create' | 'execute' | 'investigate'

export interface AdScope {
  combos?: { platform: string; country: string }[]
  campaigns?: string[]
  products?: string[]
}

export interface AdDeclaration {
  seq: number
  kind: AdTaskKind
  scope: AdScope
  user_turn: number
  created_at: string
}

/**
 * The declaration in force when `timestamp` was produced.
 *
 * A task is a SEQUENCE of phases: "create a listing for widget-006" can
 * be followed, in the same conversation, by "now audit the ads for it".
 * The create result must not get a console; the audit result must.
 *
 * Rebuilt history collapses to a single result item stamped with the
 * rebuild time, so the `created_at <= timestamp` rule resolves to the
 * latest declaration there — which is the right answer for it. A live
 * stream carrying two real result items gets each bound to its own
 * phase by the same rule.
 */
export function declarationFor(
  decls: AdDeclaration[] | null | undefined,
  timestamp: string | undefined,
): AdDeclaration | null {
  if (!decls?.length) return null
  const at = timestamp ? Date.parse(timestamp) : NaN
  // No usable timestamp — a synthetic stamp, or none at all. "Latest" is
  // the only answer available and the right one for a single collapsed
  // result item.
  if (Number.isNaN(at)) return decls[decls.length - 1]
  const eligible = decls.filter((d) => {
    const t = Date.parse(d.created_at)
    return Number.isNaN(t) || t <= at
  })
  // A real timestamp with NO declaration in force at that moment means
  // this result predates any ad work — so it gets no console. Falling
  // back to the latest here put a second, wrong console on turn 1's
  // revenue answer as soon as earlier results started rendering in their
  // own right: turn 2's audit declaration was retroactively applied to a
  // result produced before it existed.
  return eligible.length ? eligible[eligible.length - 1] : null
}

/**
 * Only an audit opens the console.
 *
 * A create is deliberately not console-gated: it is additive, capped by
 * its daily budget, reversible with a pause, and the reviewer loop
 * already catches its errors before the run ends. The console earns its
 * cost when rows need a decision each against money already spending.
 */
export function opensConsole(decl: AdDeclaration | null): boolean {
  return decl?.kind === 'audit'
}

/** Absent combos mean the whole store — same rule as the server's. */
export function isWholeStore(scope: AdScope | null | undefined): boolean {
  return !scope?.combos?.length
}

function comboInScope(scope: AdScope, platform: string, country: string) {
  if (isWholeStore(scope)) return true
  const p = (platform || '').trim().toLowerCase()
  const c = (country || '').trim().toUpperCase()
  return (scope.combos || []).some(
    (x) =>
      (x.platform || '').trim().toLowerCase() === p &&
      (x.country || '').trim().toUpperCase() === c,
  )
}

function campaignInScope(scope: AdScope, campaign: AuditCampaign) {
  const ids = scope.campaigns || []
  if (!ids.length) return true
  const id = (campaign.id || '').trim()
  const name = (campaign.name || '').trim()
  // Match on id primarily; a report that only printed the name still
  // resolves, because the console must not silently drop a campaign the
  // user explicitly asked about.
  return ids.some((i) => {
    const want = String(i).trim()
    return want !== '' && (want === id || want === name)
  })
}

/**
 * The document reduced to what the phase declared it was about.
 *
 * The obligation being scoped is what stops out-of-scope rows being
 * PRODUCED; this stops them being ACTED ON if they show up anyway. Two
 * mechanisms on purpose — the second is what still protects the user on
 * the day a prompt change makes an agent verbose again.
 */
export function narrowToScope(
  doc: AuditDoc,
  decl: AdDeclaration | null,
): AuditDoc {
  const s = decl?.scope
  // Nothing declared, or declared as the whole store with no campaign
  // list: there is nothing to narrow to.
  if (!s || (isWholeStore(s) && !s.campaigns?.length)) return doc
  const sections = doc.sections
    .filter((sec) => comboInScope(s, sec.platform, sec.country))
    .map((sec) => ({
      ...sec,
      campaigns: sec.campaigns.filter((c) => campaignInScope(s, c)),
    }))
    .filter((sec) => sec.campaigns.length > 0)
  return { ...doc, sections }
}

/** How many campaigns the scope filter removed — shown to the reviewer. */
export function droppedCampaigns(doc: AuditDoc, narrowed: AuditDoc): number {
  const count = (d: AuditDoc) =>
    d.sections.reduce((n, s) => n + s.campaigns.length, 0)
  return Math.max(0, count(doc) - count(narrowed))
}
