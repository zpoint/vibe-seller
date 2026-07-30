// Shape of a parsed ad-audit report.
//
// The report markdown is the ONE source of truth: the console renders from
// it rather than from a second artifact the agent also has to write, so a
// page improvement never needs an audit re-run and the two can never
// disagree. See app/skills_v2/amazon-ads/references/output-spec.md for the
// contract these types read.

/** Which layer a row belongs to — bids live on targets, not on queries. */
export type Layer = 'kw' | 'st'

/**
 * Primary action. `add`/`neg` additionally carry a match type; `negt`
 * (drop a category/product placement) does not, because match mode is a
 * property of keywords.
 */
export type ActionCode =
  | 'raise'
  | 'lower'
  | 'pause'
  | 'keep'
  | 'add'
  | 'neg'
  | 'negt'

export type MatchType = 'exact' | 'phrase'

/**
 * A choice as stored: `act`, or `act:matchType` when the action carries
 * one. Deliberately a string — the "did the reviewer override this?" check
 * compares choices for equality, and an object would compare by identity.
 */
export type Choice = string

/** Why a row is worth a reviewer's attention. Ordered by severity. */
export type Problem = 'bleeding' | 'under' | 'noorder' | 'noclick' | 'idle'

export interface AuditRow {
  layer: Layer
  /** Stable within a campaign+layer; combined with the campaign id for keys. */
  uid: string
  term: string
  /** Source keyword that surfaced this query; '' or '-' when unknown. */
  src: string
  match: string
  bid: number | null
  clicks: number | null
  spend: number | null
  orders: number | null
  sales: number | null
  acos: string
  roas: number | null
  /** The audit's recommendation, verbatim. */
  advice: string
  /** Parsed from `advice`; null when it named no executable action. */
  sugg: Choice | null
  /**
   * Whether the AUDIT named the match type, as opposed to the console
   * falling back to the narrower option. Kept separate so a fallback is
   * never presented as the audit's call. null when no match type applies.
   */
  mtFromAudit: boolean | null
  /** Why `sugg` is null, in the reviewer's words. */
  ambiguous: string | null
  /** Target bid for raise/lower. */
  to: number | null
  flag?: 'no_clicks'
  /**
   * True when this row's bid advice was derived from a CPC that could not
   * be computed (spend > 0 with 0 clicks). Only lower/trim advice depends
   * on CPC, so only that is unsafe.
   */
  bidUnsafe?: boolean
}

export interface Reconciliation {
  tSpend: number | null
  tClicks: number | null
  sSpend: number | null
  sClicks: number | null
  ok: boolean
}

export interface AuditCampaign {
  id: string
  name: string
  type: string
  /** Ad groups (Amazon) or SKUs (noon) — whichever the report labelled. */
  adGroups: string[]
  platform: string
  country: string
  currency: string
  spend: number | null
  sales: number | null
  orders: number | null
  roas: number | null
  /**
   * Set when the report says this campaign's own figures disagree with
   * themselves. Its bid advice must not be executable.
   */
  quarantine: string | null
  recon: Reconciliation | null
  kw: AuditRow[]
  st: AuditRow[]
}

export interface AuditSection {
  platform: string
  country: string
  label: string
  /** Campaigns drilled / active, from the 进度 line. */
  drilled: number | null
  active: number | null
  campaigns: AuditCampaign[]
}

export interface MarketRollup {
  platform: string
  country: string
  n: number | null
  spend: number | null
  sales: number | null
  orders: number | null
  roas: number | null
  currency: string
}

export interface AuditSummary {
  markets: MarketRollup[]
  /** Breakeven ROAS; every problem threshold is relative to this. */
  breakeven: number | null
}

export interface AuditDoc {
  sections: AuditSection[]
  summary: AuditSummary
}

/** One row of the decision set handed to the agent. */
export interface Decision {
  layer: Layer
  row_uid: string
  term: string
  source_keyword: string | null
  match: string | null
  clicks: number | null
  spend: number | null
  current_bid: number | null
  action: ActionCode
  action_label: string
  match_type: MatchType | null
  match_type_label: string | null
  /**
   * 'audit' when the report named the match type, 'page_default' when the
   * console filled in the narrower option. An executor should treat
   * page_default as a choice still owed, not as an instruction.
   */
  match_type_source: 'audit' | 'page_default' | null
  target_bid: number | null
  agent_suggested: Choice | null
  agent_suggested_label: string | null
  overridden: boolean
  locked_by_server: boolean
  data_flag: string | null
  agent_advice: string
}

export interface DecisionCampaign {
  campaign_id: string
  campaign_name: string | null
  platform: string
  currency: string
  quarantined: boolean
  rows: Decision[]
}

export interface DecisionMarket {
  country: string
  currency: string
  campaigns: DecisionCampaign[]
}

/** At which level the reviewer dropped something. */
export type ExclusionLevel = 'country' | 'platform' | 'campaign'

export interface ExcludedCampaign {
  campaign_id: string
  campaign_name: string | null
  country: string
  platform: string
  /** Which level the drop was made at — a campaign dropped with its whole
   *  market is a different statement from one dropped on its own. */
  excluded_at: ExclusionLevel
}

/**
 * What the reviewer submits.
 *
 * `markets` is what to DO. `excluded` is what the reviewer deliberately
 * chose not to touch — recorded explicitly because absence alone is
 * ambiguous: a campaign missing from the payload could mean the audit never
 * covered it, or that a human decided to leave it alone this round. Those
 * are different facts, and both the executing agent and the next audit need
 * to tell them apart.
 */
export interface DecisionSubmission {
  markets: DecisionMarket[]
  excluded: {
    countries: string[]
    /** `"<country>/<platform>"`. */
    platforms: string[]
    campaigns: ExcludedCampaign[]
  }
  totals: {
    campaigns_in_scope: number
    campaigns_excluded: number
    rows_to_change: number
  }
}
