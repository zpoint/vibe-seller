// A reviewer rarely executes a whole report at once. These pin what
// "submit only this much" means, and that the same page works whether the
// report covers one marketplace or several.

import { describe, expect, it } from 'vitest'
import { parseReport } from '../lib/adAudit/parseReport'
import {
  buildDecisionSet,
  buildReviewState,
  buildSubmission,
  chooseAction,
  initialChoices,
  rowKey,
  totalsOf,
} from '../lib/adAudit/review'
import {
  NOTHING_EXCLUDED,
  campaignInScope,
  campaignKey,
  countryKey,
  keepOnly,
  platformKey,
  platformsOf,
  scopeCounts,
  toggleScope,
} from '../lib/adAudit/scope'

function section(platform: string, country: string, campaigns: string[]) {
  const head = `## ${platform} ${country}
**进度**: drilled ${campaigns.length}/${campaigns.length} active (${campaigns.length} total, 1 pages)

| id | name | type | spend (SAR) | sales | orders | roas |
|---|---|---|---|---|---|---|
${campaigns.map((id) => `| ${id} | acme ${id} | Auto | 120.00 | 400.00 | 20 | 3.33 |`).join('\n')}
`
  const blocks = campaigns
    .map(
      (id) => `
### ${id} | acme ${id} | Auto | AG: acme group

| 定向词 | 匹配 | 出价 | 点击 | 花费 (SAR) | 订单 | ROAS | 建议 |
|---|---|---|---|---|---|---|---|
| widget red | Exact | 2.00 | 40 | 80.00 | 4 | 5.00 | 维持 |

| 搜索词 | 来源关键词 | 匹配 | 点击 | 花费 (SAR) | 订单 | ROAS | 建议 |
|---|---|---|---|---|---|---|---|
| red widget | widget red | Broad | 30 | 60.00 | 0 | — | 维持 |
`,
    )
    .join('')
  return head + blocks
}

const SUMMARY = `
## 汇总建议

| 平台 | 市场 | 活动数 | 花费 | 销售额 | 订单 | ROAS |
|---|---|---|---|---|---|---|
| amazon | SA | 1 | SAR 120.00 | 400.00 | 20 | 3.33 |

盈亏线 ROAS = 3.33
`

/** One market, one platform — the smallest shape the page must support. */
const SINGLE = '# Ad audit\n\n' + section('amazon', 'SA', ['100000000001']) + SUMMARY

/** Three markets, two platforms each — the large shape. */
const WIDE =
  '# Ad audit\n\n' +
  section('amazon', 'SA', ['100000000001', '100000000002']) +
  section('noon', 'SA', ['C_SA1']) +
  section('amazon', 'AE', ['100000000003']) +
  section('noon', 'AE', ['C_AE1']) +
  section('amazon', 'AU', ['100000000004']) +
  section('noon', 'AU', ['C_AU1']) +
  SUMMARY

const campaignsOf = (md: string) =>
  buildReviewState(parseReport(md)).campaigns

describe('report shapes', () => {
  it('handles a one-market one-platform report', () => {
    const cs = campaignsOf(SINGLE)
    expect(cs).toHaveLength(1)
    expect(platformsOf(cs)).toEqual([{ country: 'SA', platform: 'amazon' }])
    expect(scopeCounts(cs, NOTHING_EXCLUDED)).toEqual({
      countries: 1,
      platforms: 1,
      campaigns: 1,
      droppedCampaigns: 0,
    })
  })

  it('handles three markets with two platforms each', () => {
    const cs = campaignsOf(WIDE)
    expect(cs).toHaveLength(7)
    expect(platformsOf(cs)).toHaveLength(6)
    expect(scopeCounts(cs, NOTHING_EXCLUDED)).toMatchObject({
      countries: 3,
      platforms: 6,
      campaigns: 7,
    })
  })
})

describe('scope nesting', () => {
  it('dropping a country drops its platforms and campaigns', () => {
    const cs = campaignsOf(WIDE)
    const ex = toggleScope(NOTHING_EXCLUDED, countryKey('SA'))
    expect(cs.filter((c) => campaignInScope(c, ex)).map((c) => c.country)).toEqual(
      ['AE', 'AE', 'AU', 'AU'],
    )
    expect(scopeCounts(cs, ex)).toMatchObject({ countries: 2, campaigns: 4 })
  })

  it('dropping a platform only affects that platform in that country', () => {
    const cs = campaignsOf(WIDE)
    const ex = toggleScope(NOTHING_EXCLUDED, platformKey('SA', 'amazon'))
    const left = cs.filter((c) => campaignInScope(c, ex))
    // noon SA survives; amazon AE survives.
    expect(left.some((c) => c.country === 'SA' && c.platform === 'noon')).toBe(true)
    expect(left.some((c) => c.country === 'AE' && c.platform === 'amazon')).toBe(true)
    expect(left.some((c) => c.country === 'SA' && c.platform === 'amazon')).toBe(false)
  })

  it('keepOnly leaves exactly one campaign in scope', () => {
    const cs = campaignsOf(WIDE)
    const ex = keepOnly(cs, 'C_AE1')
    const left = cs.filter((c) => campaignInScope(c, ex))
    expect(left.map((c) => c.id)).toEqual(['C_AE1'])
  })

  it('is exclusion-based, so a market added by a later report is in scope', () => {
    // An inclusion model would silently drop anything nobody had ticked.
    const cs = campaignsOf(WIDE)
    expect(cs.every((c) => campaignInScope(c, NOTHING_EXCLUDED))).toBe(true)
  })

  it('toggling never mutates the previous set', () => {
    const a = NOTHING_EXCLUDED
    const b = toggleScope(a, countryKey('SA'))
    expect(a.has(countryKey('SA'))).toBe(false)
    expect(b.has(countryKey('SA'))).toBe(true)
    expect(toggleScope(b, countryKey('SA')).has(countryKey('SA'))).toBe(false)
  })
})

describe('submitting a narrowed scope', () => {
  it('omits dropped campaigns entirely, not as empty entries', () => {
    // The agent acts on what it receives; a dropped market must be ABSENT.
    const doc = parseReport(WIDE)
    const state = buildReviewState(doc)
    let ch = initialChoices(state)
    for (const c of state.campaigns) {
      ch = chooseAction(ch, rowKey(c.id, c.kw[0]), 'pause')
    }
    const all = buildDecisionSet(state, ch)
    expect(all.flatMap((m) => m.campaigns)).toHaveLength(7)

    const ex = keepOnly(state.campaigns, 'C_AE1')
    const narrowed = buildDecisionSet(state, ch, ex)
    expect(narrowed).toHaveLength(1)
    expect(narrowed[0].country).toBe('AE')
    expect(narrowed[0].campaigns.map((c) => c.campaign_id)).toEqual(['C_AE1'])
  })

  it('does not count a change on a dropped campaign in the footer', () => {
    // The footer is a promise about what will happen.
    const doc = parseReport(WIDE)
    const state = buildReviewState(doc)
    const target = state.campaigns[0]
    const ch = chooseAction(
      initialChoices(state),
      rowKey(target.id, target.kw[0]),
      'pause',
    )
    expect(totalsOf(state, ch).acting).toBe(1)
    const ex = toggleScope(NOTHING_EXCLUDED, campaignKey(target.id))
    expect(totalsOf(state, ch, ex).acting).toBe(0)
  })

  it('a single-market report needs no scoping to submit', () => {
    const doc = parseReport(SINGLE)
    const state = buildReviewState(doc)
    const c = state.campaigns[0]
    const ch = chooseAction(initialChoices(state), rowKey(c.id, c.kw[0]), 'pause')
    const set = buildDecisionSet(state, ch, NOTHING_EXCLUDED)
    expect(set).toHaveLength(1)
    expect(set[0].campaigns[0].rows).toHaveLength(1)
  })
})

describe('rows_to_change counts work, not payload size', () => {
  it('excludes an overridden keep from the count', () => {
    // A keep travels in the payload when it OVERRIDES the audit — the
    // executor needs to know the human said hold — but it is not work,
    // and counting it overstated the run in the footer and the follow-up.
    const md =
      '# Ad audit\n\n' +
      `## amazon SA
**进度**: drilled 1/1 active (1 total, 1 pages)

| id | name | type | spend (SAR) | sales | orders | roas |
|---|---|---|---|---|---|---|
| 100000000001 | acme one | Auto | 120.00 | 400.00 | 20 | 3.33 |

### 100000000001 | acme one | Auto | AG: acme group

| 定向词 | 匹配 | 出价 | 点击 | 花费 (SAR) | 订单 | ROAS | 建议 |
|---|---|---|---|---|---|---|---|
| widget red | Exact | 2.00 | 40 | 80.00 | 4 | 5.00 | 维持 |

| 搜索词 | 来源关键词 | 匹配 | 点击 | 花费 (SAR) | 订单 | ROAS | 建议 |
|---|---|---|---|---|---|---|---|
| bad widget | widget red | Broad | 30 | 60.00 | 0 | — | 否定词组（零转化） |
` + SUMMARY
    const state = buildReviewState(parseReport(md))
    const c = state.campaigns[0]
    let ch = initialChoices(state)
    // one real change...
    ch = chooseAction(ch, rowKey(c.id, c.kw[0]), 'pause')
    // ...and one OVERRIDE of the audit's negation back to keep
    ch = chooseAction(ch, rowKey(c.id, c.st[0]), 'keep')
    const sub = buildSubmission(state, ch)
    const all = sub.markets.flatMap((m) => m.campaigns.flatMap((x) => x.rows))
    expect(all.map((r) => r.action).sort()).toEqual(['keep', 'pause'])
    expect(sub.totals.rows_to_change).toBe(1)
  })
})
