// The action vocabulary, and the composite `act:matchType` encoding.
//
// Two things this file exists to keep straight:
//
// 1. A search-term decision has TWO parts. 「拓词」 was jargon, and a flat
//    button row hid an asymmetry — negation exposed its match type while
//    ADDING a keyword did not, though you must choose one there too. So the
//    reviewer answers "what" first and "how broadly" second.
// 2. Match mode is a property of KEYWORDS. A category/product placement
//    (noon `Subcat`, Amazon `Category`, an ASIN) has no phrase-vs-exact
//    variant and cannot be promoted to a keyword, so it gets its own,
//    shorter vocabulary. Offering the keyword verbs there asked a reviewer
//    to choose between two things that do not exist.
//
// Codes are the only thing stored or compared. There are TWO label sets and
// they are not interchangeable:
//
//   * i18n keys -> what the REVIEWER sees, in their chosen language.
//   * CANONICAL_* -> what goes in the decision file handed to the agent.
//     Those stay in the report's own vocabulary (see output-spec.md) because
//     they are an agent-facing contract, not UI: the executor must read the
//     same verb whether the reviewer happened to be in English or Chinese.

import type { ActionCode, Choice, MatchType } from './types'

/** i18n keys for the reviewer-facing labels. */
export const ACT_I18N: Record<ActionCode, string> = {
  raise: 'audit.act.raise',
  lower: 'audit.act.lower',
  pause: 'audit.act.pause',
  keep: 'audit.act.keep',
  add: 'audit.act.add',
  neg: 'audit.act.neg',
  negt: 'audit.act.negt',
}

export const MT_I18N: Record<MatchType, string> = {
  exact: 'audit.match.exact',
  phrase: 'audit.match.phrase',
}

/**
 * Agent-facing labels. Deliberately NOT localised — they mirror the verbs
 * output-spec.md tells the audit to write, so the decision file speaks the
 * report's language regardless of the reviewer's UI.
 */
export const CANONICAL_ACT_LABEL: Record<ActionCode, string> = {
  raise: '提高出价',
  lower: '降低出价',
  pause: '暂停',
  keep: '维持',
  add: '添加为关键词',
  neg: '否定关键词',
  negt: '否定投放',
}

export const CANONICAL_MT_LABEL: Record<MatchType, string> = {
  exact: '精准',
  phrase: '词组',
}

/** Actions that carry a match type. */
export const MT_ACTS: ReadonlySet<ActionCode> = new Set<ActionCode>([
  'add',
  'neg',
])

/** Narrowest blast radius, so it is the safe fallback. */
export const DEFAULT_MT: MatchType = 'exact'

export const KW_ACTS: readonly ActionCode[] = [
  'raise',
  'lower',
  'pause',
  'keep',
]
/** Keyword queries. */
export const ST_ACTS: readonly ActionCode[] = ['add', 'neg', 'keep']
/** Category / ASIN placements. */
export const PRODUCT_TARGET_ACTS: readonly ActionCode[] = ['negt', 'keep']

export const MATCH_TYPES: readonly MatchType[] = ['exact', 'phrase']

const PRODUCT_TARGET_RE = /subcat|categor|product|asin|品类|类目|商品/i

/** True when a row's match column names a placement rather than a keyword. */
export function isProductTarget(match: string | null | undefined): boolean {
  return PRODUCT_TARGET_RE.test(match || '')
}

/** The buttons a search-term row should offer, given its own match type. */
export function stActs(match: string | null | undefined): readonly ActionCode[] {
  return isProductTarget(match) ? PRODUCT_TARGET_ACTS : ST_ACTS
}

export function actOf(v: Choice | null | undefined): ActionCode | null {
  if (v == null) return null
  return String(v).split(':')[0] as ActionCode
}

export function mtOf(v: Choice | null | undefined): MatchType | null {
  if (v == null) return null
  const mt = String(v).split(':')[1]
  return mt ? (mt as MatchType) : null
}

/** Build a choice, attaching a match type only where one applies. */
export function joinAct(
  act: ActionCode | null,
  mt?: MatchType | null,
): Choice | null {
  if (act == null) return null
  return MT_ACTS.has(act) ? `${act}:${mt || DEFAULT_MT}` : act
}

/** Agent-facing label for a composite choice. */
export function canonicalLabelOf(v: Choice | null | undefined): string {
  const a = actOf(v)
  if (a == null) return ''
  const mt = mtOf(v)
  return (
    (CANONICAL_ACT_LABEL[a] || a) +
    (mt ? ` · ${CANONICAL_MT_LABEL[mt] || mt}` : '')
  )
}

/** A choice that changes something. 维持 is a decision, not a change. */
export function isChange(v: Choice | null | undefined): boolean {
  return v != null && actOf(v) !== 'keep'
}
