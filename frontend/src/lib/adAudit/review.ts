// The review model: what counts as a problem, what the page opens on, and
// what gets handed to the agent.

import type {
  ActionCode,
  DecisionSubmission,
  ExcludedCampaign,
  AuditCampaign,
  AuditDoc,
  AuditRow,
  Choice,
  Decision,
  DecisionMarket,
  MatchType,
  Problem,
} from './types'
import type { ExcludedScopes } from './scope'
import {
  NOTHING_EXCLUDED,
  campaignInScope,
  countryKey,
  platformKey,
} from './scope'
import {
  CANONICAL_ACT_LABEL,
  CANONICAL_MT_LABEL,
  MT_ACTS,
  actOf,
  canonicalLabelOf,
  isChange,
  joinAct,
  mtOf,
} from './vocabulary'

/**
 * Below this, a ratio is noise rather than a decision. ≈US$3.
 *
 * Without the floor, most rows on a real report light up: a sub-currency-unit
 * spend with no order is ordinary, and tinting it makes the tint mean nothing.
 */
export const MATERIAL_SPEND = 10

/** Bleeding vs merely under: 60% of breakeven separates them. */
const BLEEDING_FRACTION = 0.6

export function rowKey(campaignId: string, row: AuditRow): string {
  return `${campaignId}|${row.uid}`
}

export function hasRealName(c: AuditCampaign): boolean {
  return !!(c.name && c.name !== c.id)
}

/**
 * Why a row is worth looking at. Three distinct problems, not one:
 *
 *  - SPENT WITH NO CLICKS — money left and nobody arrived. Impossible on
 *    CPC billing, so it marks a data defect and is flagged at ANY amount.
 *    It is also the row whose CPC cannot be computed.
 *  - SPENT WITH NO ORDERS — clicks arrived and none converted.
 *  - BELOW BREAKEVEN — it converts, but at a loss.
 *
 * A row with no spend at all is not a problem, it is idle: worth SEEING,
 * since you cannot judge a target on no data, but it is not losing money.
 * In a typical manual campaign five of seven rows are idle, so colouring
 * them like a loss would drown the row that actually is bleeding.
 */
export function problemsOf(
  row: AuditRow,
  breakeven: number | null,
): Problem[] {
  const out: Problem[] = []
  const spend = row.spend || 0
  if (spend <= 0) {
    if (row.clicks === 0) out.push('idle')
    return out
  }
  if (row.clicks === 0) out.push('noclick')
  if (spend >= MATERIAL_SPEND) {
    if (row.clicks !== 0 && (row.orders || 0) === 0) out.push('noorder')
    if (row.roas != null && breakeven && row.roas < breakeven) {
      out.push(row.roas < breakeven * BLEEDING_FRACTION ? 'bleeding' : 'under')
    }
  }
  return out
}

export function roasClass(
  v: number | null,
  breakeven: number | null,
): '' | 'neg' | 'warn' | 'pos' {
  if (v == null) return ''
  if (!breakeven) return 'pos'
  if (v >= breakeven) return 'pos'
  return v < breakeven * BLEEDING_FRACTION ? 'neg' : 'warn'
}

/** The reviewer's choices, keyed by row. Immutable — every edit returns a
 *  new map, so React can see the change and nothing can mutate it behind a
 *  component's back. */
export type ChoiceMap = ReadonlyMap<string, Choice | null>

/**
 * Reviewer-set target bids, by row key.
 *
 * A raise/lower is only executable if it says HOW MUCH. The audit
 * supplies a number when it proposed the move itself; when the reviewer
 * overrides a 维持 into a raise there is no proposal to inherit, and
 * without this the agent received `action: raise, target_bid: null` —
 * the same "name the exact action" ambiguity the report format exists to
 * prevent, just moved into the payload.
 */
export type TargetBidMap = ReadonlyMap<string, number>

export const NO_TARGET_BIDS: TargetBidMap = new Map<string, number>()

/** The bid a raise/lower should carry: the reviewer's, else the audit's. */
export function effectiveTargetBid(
  row: AuditRow,
  key: string,
  targetBids: TargetBidMap = NO_TARGET_BIDS,
): number | null {
  const set = targetBids.get(key)
  return set != null && Number.isFinite(set) ? set : row.to
}

/**
 * Everything about a report that does NOT change while reviewing: the rows,
 * the post-lock baseline, the breakeven. Choices live separately, in React
 * state, so the model stays pure functions of (state, choices).
 */
export interface ReviewState {
  /** Every row, by key. */
  rows: Map<string, { campaign: AuditCampaign; row: AuditRow }>
  /** The opening state, AFTER the safety locks — the override baseline. */
  base: Map<string, Choice | null>
  /** Rows the server locked to 维持 regardless of what the audit advised. */
  forced: number
  breakeven: number | null
  campaigns: AuditCampaign[]
}

/**
 * Seed the review from a parsed report.
 *
 * Two safety locks are applied before the reviewer arrives, and the override
 * baseline is taken AFTER them. "I overrode the agent" has to mean the
 * reviewer changed something — counted against the audit's raw suggestion
 * instead, the footer opened claiming 34 overrides on an untouched page.
 */
export function buildReviewState(doc: AuditDoc): ReviewState {
  const campaigns = doc.sections.flatMap((s) => s.campaigns)
  const rows = new Map<string, { campaign: AuditCampaign; row: AuditRow }>()
  const choice = new Map<string, Choice | null>()
  for (const c of campaigns) {
    for (const r of [...c.kw, ...c.st]) {
      const k = rowKey(c.id, r)
      rows.set(k, { campaign: c, row: r })
      // A quarantined campaign must not carry executable choices: its own
      // block says its bid advice cannot be executed. A row whose CPC could
      // not be computed must not carry a CPC-derived bid target.
      choice.set(k, c.quarantine ? 'keep' : r.bidUnsafe ? 'keep' : r.sugg)
    }
  }
  const base = new Map(choice)
  let forced = 0
  for (const [k, v] of base) {
    if (v !== rows.get(k)!.row.sugg) forced++
  }
  return { rows, base, forced, breakeven: doc.summary.breakeven, campaigns }
}

/** The choices the page opens on — the baseline, as a starting point. */
export function initialChoices(state: ReviewState): ChoiceMap {
  return new Map(state.base)
}

/**
 * Set a primary action, carrying the existing match type over where one
 * applies — switching add <-> negate must not re-ask "how broadly" when the
 * reviewer already answered it.
 */
export function chooseAction(
  choices: ChoiceMap,
  key: string,
  act: ActionCode,
): ChoiceMap {
  const next = new Map(choices)
  next.set(key, joinAct(act, mtOf(choices.get(key))))
  return next
}

/** Set the match type, keeping the primary action. */
export function chooseMatchType(
  choices: ChoiceMap,
  key: string,
  mt: MatchType,
): ChoiceMap {
  const next = new Map(choices)
  next.set(key, joinAct(actOf(choices.get(key)), mt))
  return next
}

/**
 * Reset a layer to the audit's advice, or to 维持 across the board.
 *
 * Restoring the agent's advice must NOT undo a safety lock: a quarantined
 * campaign and a row whose CPC could not be computed stay at 维持.
 */
export function bulkSet(
  choices: ChoiceMap,
  campaign: AuditCampaign,
  layer: 'kw' | 'st',
  mode: 'sugg' | 'keep',
): ChoiceMap {
  const next = new Map(choices)
  for (const r of layer === 'kw' ? campaign.kw : campaign.st) {
    next.set(
      rowKey(campaign.id, r),
      mode === 'keep'
        ? 'keep'
        : campaign.quarantine || r.bidUnsafe
          ? 'keep'
          : r.sugg,
    )
  }
  return next
}

export interface ReviewTotals {
  acting: number
  edited: number
  keeping: number
  /** Spend under a changed row, per currency. Not summed across markets. */
  impact: Map<string, number>
}

export function totalsOf(
  state: ReviewState,
  choices: ChoiceMap,
  excluded: ExcludedScopes = NOTHING_EXCLUDED,
): ReviewTotals {
  const impact = new Map<string, number>()
  let acting = 0
  let edited = 0
  let keeping = 0
  for (const [k, cur] of choices) {
    const entry = state.rows.get(k)
    if (!entry) continue
    // A change on a dropped campaign is not being submitted, so it must not
    // be counted as one — the footer is a promise about what will happen.
    if (!campaignInScope(entry.campaign, excluded)) continue
    if (isChange(cur)) {
      acting++
      const cur_ = entry.campaign.currency
      impact.set(cur_, (impact.get(cur_) || 0) + (entry.row.spend || 0))
    } else {
      keeping++
    }
    if (cur !== state.base.get(k)) edited++
  }
  return { acting, edited, keeping, impact }
}

export function campaignChangeCount(
  choices: ChoiceMap,
  c: AuditCampaign,
): number {
  return [...c.kw, ...c.st].filter((r) =>
    isChange(choices.get(rowKey(c.id, r))),
  ).length
}

/**
 * The decision set — exactly what gets posted back to the task.
 *
 * Only rows that DO something, or that the reviewer moved off the audit's
 * suggestion, are included: an executor should not have to sift a thousand
 * 维持 rows to find the dozen that matter.
 *
 * Every row carries both the code and its label, so the file is executable
 * on its own terms. A bare `neg` means nothing to a reader who has not seen
 * this vocabulary — the same unwritten-contract problem that once made a
 * correct 「否定商品定向」 read as ambiguous, moved one step downstream.
 */
export function buildDecisionSet(
  state: ReviewState,
  choices: ChoiceMap,
  excluded: ExcludedScopes = NOTHING_EXCLUDED,
  targetBids: TargetBidMap = NO_TARGET_BIDS,
): DecisionMarket[] {
  const byCountry = new Map<string, AuditCampaign[]>()
  for (const c of state.campaigns) {
    // Out-of-scope campaigns are not "keep", they are ABSENT: the agent is
    // told to act on what it receives, so a dropped market must not appear
    // at all rather than appear with no rows.
    if (!campaignInScope(c, excluded)) continue
    if (!byCountry.has(c.country)) byCountry.set(c.country, [])
    byCountry.get(c.country)!.push(c)
  }
  const out: DecisionMarket[] = []
  for (const [country, campaigns] of byCountry) {
    const decided = []
    for (const c of campaigns) {
      const rows: Decision[] = []
      for (const r of [...c.kw, ...c.st]) {
        const k = rowKey(c.id, r)
        const cur = choices.get(k) ?? null
        if (!isChange(cur) && cur === r.sugg) continue
        const act = actOf(cur)
        if (act == null) continue
        const mt = mtOf(cur)
        rows.push({
          layer: r.layer,
          row_uid: r.uid,
          term: r.term,
          source_keyword: r.src && r.src !== '-' ? r.src : null,
          match: r.match || null,
          clicks: r.clicks,
          spend: r.spend,
          current_bid: r.bid,
          action: act,
          action_label: CANONICAL_ACT_LABEL[act] || act,
          match_type: mt,
          match_type_label: mt ? CANONICAL_MT_LABEL[mt] : null,
          match_type_source: !mt
            ? null
            : r.mtFromAudit
              ? 'audit'
              : 'page_default',
          target_bid:
            act === 'raise' || act === 'lower'
              ? effectiveTargetBid(r, k, targetBids)
              : null,
          agent_suggested: r.sugg,
          agent_suggested_label: r.sugg ? canonicalLabelOf(r.sugg) : null,
          overridden: cur !== state.base.get(k),
          locked_by_server: state.base.get(k) !== r.sugg,
          data_flag: r.flag ?? null,
          agent_advice: r.advice,
        })
      }
      if (rows.length) {
        decided.push({
          campaign_id: c.id,
          campaign_name: hasRealName(c) ? c.name : null,
          platform: c.platform,
          currency: c.currency,
          quarantined: !!c.quarantine,
          rows,
        })
      }
    }
    if (decided.length) {
      out.push({
        country,
        currency: campaigns[0]?.currency || '',
        campaigns: decided,
      })
    }
  }
  return out
}

/** Actions that still need a match type answered by the reviewer. */
export function needsMatchType(cur: Choice | null | undefined): boolean {
  const a = actOf(cur)
  return a != null && MT_ACTS.has(a)
}

/**
 * The few numbers the entry card shows without opening the console.
 *
 * Lives here rather than beside the component so the card and the console
 * cannot disagree about what "rows" or "to change" mean.
 */
export function auditHeadline(doc: AuditDoc) {
  const campaigns = doc.sections.flatMap((s) => s.campaigns)
  const state = buildReviewState(doc)
  let acting = 0
  for (const v of state.base.values()) if (isChange(v)) acting++
  return {
    markets: new Set(campaigns.map((c) => c.country)).size,
    campaigns: campaigns.length,
    rows: campaigns.reduce((a, c) => a + c.kw.length + c.st.length, 0),
    acting,
    quarantined: campaigns.filter((c) => c.quarantine).length,
    breakeven: doc.summary.breakeven,
  }
}

/**
 * The full submission: what to act on, and what was deliberately left out.
 *
 * The exclusions are enumerated from the campaigns actually in the report,
 * not from the raw key set — so the record names real campaigns a reader can
 * look up, and a stale key for a campaign that is no longer in the report
 * cannot invent one.
 */
export function buildSubmission(
  state: ReviewState,
  choices: ChoiceMap,
  excluded: ExcludedScopes = NOTHING_EXCLUDED,
  targetBids: TargetBidMap = NO_TARGET_BIDS,
): DecisionSubmission {
  const markets = buildDecisionSet(state, choices, excluded, targetBids)
  const outCountries = new Set<string>()
  const outPlatforms = new Set<string>()
  const campaigns: ExcludedCampaign[] = []
  for (const c of state.campaigns) {
    if (campaignInScope(c, excluded)) continue
    // Report the OUTERMOST level that dropped it: "excluded with its market"
    // is the honest description, not "campaign excluded".
    const level = excluded.has(countryKey(c.country))
      ? 'country'
      : excluded.has(platformKey(c.country, c.platform))
        ? 'platform'
        : 'campaign'
    if (level === 'country') outCountries.add(c.country)
    if (level === 'platform') outPlatforms.add(`${c.country}/${c.platform}`)
    campaigns.push({
      campaign_id: c.id,
      campaign_name: hasRealName(c) ? c.name : null,
      country: c.country,
      platform: c.platform,
      excluded_at: level,
    })
  }
  // Rows that DO something. A keep is included in the payload when the
  // reviewer overrode the audit's advice — the executor needs to know the
  // human said hold — but counting it as work would overstate what is
  // about to happen, in the footer and in the follow-up message.
  const rows = markets.reduce(
    (a, m) =>
      a +
      m.campaigns.reduce(
        (b, c) => b + c.rows.filter((r) => r.action !== 'keep').length,
        0,
      ),
    0,
  )
  const missingBid = markets.reduce(
    (a, m) =>
      a +
      m.campaigns.reduce(
        (b, c) =>
          b +
          c.rows.filter(
            (r) =>
              (r.action === 'raise' || r.action === 'lower') &&
              r.target_bid == null,
          ).length,
        0,
      ),
    0,
  )
  return {
    markets,
    excluded: {
      countries: [...outCountries],
      platforms: [...outPlatforms],
      campaigns,
    },
    totals: {
      campaigns_in_scope: state.campaigns.length - campaigns.length,
      campaigns_excluded: campaigns.length,
      rows_to_change: rows,
      /** Bid moves with no amount — must be 0 before this can be sent. */
      rows_missing_bid: missingBid,
    },
  }
}
