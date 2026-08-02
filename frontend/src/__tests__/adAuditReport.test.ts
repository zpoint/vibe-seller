// The audit console renders from the report markdown, so the parser IS the
// contract. These pin the decisions that were expensive to get right on live
// data — every one of them was a defect first.

import { describe, expect, it } from 'vitest'
import {
  num,
  parseReport,
  splitTables,
  suggest,
} from '../lib/adAudit/parseReport'
import {
  buildDecisionSet,
  buildReviewState,
  bulkSet,
  chooseAction,
  chooseMatchType,
  initialChoices,
  problemsOf,
  rowKey,
  totalsOf,
} from '../lib/adAudit/review'
import { isProductTarget, stActs } from '../lib/adAudit/vocabulary'

/** A minimal report with placeholder data only. */
function report(opts: { targeting?: string; searchTerms?: string; note?: string } = {}) {
  const targeting =
    opts.targeting ??
    '| widget red | Exact | 2.00 | 40 | 80.00 | 4 | 5.00 | 维持 |\n'
  const st =
    opts.searchTerms ??
    '| red widget | widget red | Exact | 30 | 60.00 | 3 | 4.00 | 维持 |\n'
  return `# Ad audit

## amazon SA
**进度**: drilled 1/1 active (1 total, 1 pages)

| id | name | type | spend (SAR) | sales | orders | roas |
|---|---|---|---|---|---|---|
| 100000000001 | acme-widget-006 auto | Auto | 120.00 | 400.00 | 20 | 3.33 |

### 100000000001 | acme-widget-006 auto | Auto | AG: acme-widget-006 group
${opts.note ?? ''}
| 定向词 | 匹配 | 出价 | 点击 | 花费 (SAR) | 订单 | ROAS | 建议 |
|---|---|---|---|---|---|---|---|
${targeting}
| 搜索词 | 来源关键词 | 匹配 | 点击 | 花费 (SAR) | 订单 | ROAS | 建议 |
|---|---|---|---|---|---|---|---|
${st}

## 汇总建议

| 平台 | 市场 | 活动数 | 花费 | 销售额 | 订单 | ROAS |
|---|---|---|---|---|---|---|
| amazon | SA | 1 | SAR 120.00 | 400.00 | 20 | 3.33 |

盈亏线 ROAS = 3.33
`
}

describe('num', () => {
  it('reads absent data as null, never as zero', () => {
    // A zero would silently enter rollups and averages.
    for (const blank of ['—', '-', '–', '', '   ', '∞']) {
      expect(num(blank)).toBeNull()
    }
  })

  it('strips thousands separators', () => {
    expect(num('1,234.56')).toBeCloseTo(1234.56)
  })

  it('pulls a figure out of a decorated cell', () => {
    expect(num('SAR 45.30')).toBeCloseTo(45.3)
  })
})

describe('splitTables', () => {
  it('separates tables that are not blank-line delimited', () => {
    const tables = splitTables(
      '| a | b |\n|---|---|\n| 1 | 2 |\ntext\n| c |\n|---|\n| 3 |\n',
    )
    expect(tables).toHaveLength(2)
    expect(tables[0].head).toEqual(['a', 'b'])
    expect(tables[1].head).toEqual(['c'])
  })
})

describe('parseReport', () => {
  it('reads the marketplace by SHAPE, not from a fixed list', () => {
    // Three hardcoded country lists had drifted apart; a missing code
    // silently dropped a whole market from the page.
    const doc = parseReport(report().replace('amazon SA', 'amazon AU'))
    expect(doc.sections.map((s) => s.country)).toEqual(['AU'])
  })

  it('takes the campaign name and ad group from the block heading', () => {
    const c = parseReport(report()).sections[0].campaigns[0]
    expect(c.name).toBe('acme-widget-006 auto')
    expect(c.adGroups).toEqual(['acme-widget-006 group'])
  })

  it('falls back to the rollup name when the heading repeats the id', () => {
    const md = report().replace(
      '### 100000000001 | acme-widget-006 auto | Auto | AG:',
      '### 100000000001 | 100000000001 | Auto | AG:',
    )
    expect(parseReport(md).sections[0].campaigns[0].name).toBe(
      'acme-widget-006 auto',
    )
  })

  it('records the drill progress and the breakeven', () => {
    const doc = parseReport(report())
    expect(doc.sections[0].drilled).toBe(1)
    expect(doc.sections[0].active).toBe(1)
    expect(doc.summary.breakeven).toBeCloseTo(3.33)
  })

  it('sorts search terms by spend, so the money order is the review order', () => {
    const doc = parseReport(
      report({
        searchTerms:
          '| cheap widget | widget red | Broad | 2 | 4.00 | 0 | — | 维持 |\n' +
          '| big widget | widget red | Broad | 50 | 95.00 | 5 | 4.10 | 维持 |\n',
      }),
    )
    expect(doc.sections[0].campaigns[0].st.map((r) => r.term)).toEqual([
      'big widget',
      'cheap widget',
    ])
  })

  it('quarantines a campaign the report says not to execute', () => {
    const c = parseReport(
      report({ note: '> ⚠️ 数据不可信 — 请勿执行本活动的出价建议\n' }),
    ).sections[0].campaigns[0]
    expect(c.quarantine).toContain('数据不可信')
  })

  it('drops total rows so they cannot be actioned', () => {
    const doc = parseReport(
      report({
        targeting:
          '| widget red | Exact | 2.00 | 40 | 80.00 | 4 | 5.00 | 维持 |\n' +
          '| **合计** | — | — | 40 | 80.00 | 4 | 5.00 | — |\n',
      }),
    )
    expect(doc.sections[0].campaigns[0].kw).toHaveLength(1)
  })
})

describe('suggest', () => {
  it('reads the match type the audit named', () => {
    expect(suggest('否定词组（零转化）', 'st', 'Broad')).toMatchObject({
      act: 'neg',
      mt: 'phrase',
      mtFromAudit: true,
    })
    expect(suggest('否定精确（零转化）', 'st', 'Broad')).toMatchObject({
      act: 'neg',
      mt: 'exact',
      mtFromAudit: true,
    })
  })

  it('falls back to 精准 but records that the page chose it', () => {
    // The page may pre-select the narrower option; it must never present its
    // own fallback as the audit's call.
    expect(suggest('否定（零转化，浪费）', 'st', 'Broad')).toMatchObject({
      act: 'neg',
      mt: 'exact',
      mtFromAudit: false,
    })
  })

  it('accepts both the old and the clearer add spelling', () => {
    for (const advice of ['拓词（Exact）', '添加为关键词（精准，建议出价 1.10）']) {
      expect(suggest(advice, 'st', 'Broad')).toMatchObject({ act: 'add' })
    }
  })

  it('gives a placement its own vocabulary, with no match type', () => {
    // Match mode is a property of keywords. Demanding phrase-vs-exact here
    // made a correct 「否定商品定向」 read as "no executable action".
    expect(suggest('否定商品定向（零转化）', 'st', 'Subcat')).toEqual({
      act: 'negt',
    })
    expect(stActs('Subcat')).toEqual(['negt', 'keep'])
    expect(stActs('Broad')).toEqual(['add', 'neg', 'keep'])
    expect(isProductTarget('Category')).toBe(true)
    expect(isProductTarget('Phrase')).toBe(false)
  })

  it('reports an unreadable recommendation rather than guessing', () => {
    expect(suggest('看看再说', 'st', 'Broad').act).toBeNull()
    expect(suggest('', 'st', 'Broad').ambiguous).toBe('没有建议')
  })

  it('reads a bid target off a targeting row', () => {
    expect(suggest('提高至 0.96（ROAS 6>5）', 'kw')).toMatchObject({
      act: 'raise',
      to: 0.96,
    })
    expect(suggest('下调至 1.20（ACOS 高）', 'kw')).toMatchObject({
      act: 'lower',
      to: 1.2,
    })
  })

  it('treats 否定 on a TARGET as a pause', () => {
    // You cannot negate something you bid on.
    expect(suggest('否定该定向词', 'kw')).toEqual({ act: 'pause' })
  })
})

describe('problemsOf', () => {
  const row = (o: Partial<Parameters<typeof problemsOf>[0]>) =>
    ({ spend: 0, clicks: 0, orders: 0, roas: null, ...o }) as never

  it('flags spend with zero clicks at ANY amount', () => {
    // Impossible on CPC billing, so it marks a data defect, and its CPC
    // cannot be computed — the bid advice derived from it is unusable.
    expect(problemsOf(row({ spend: 0.5, clicks: 0 }), 3.33)).toContain('noclick')
  })

  it('calls a never-served row idle, not a loss', () => {
    expect(problemsOf(row({ spend: 0, clicks: 0 }), 3.33)).toEqual(['idle'])
  })

  it('ignores a trivial spend with no orders', () => {
    // Without the material-spend floor 65% of a live report lit up.
    expect(problemsOf(row({ spend: 0.78, clicks: 1, orders: 0 }), 3.33)).toEqual(
      [],
    )
  })

  it('separates bleeding from merely under breakeven', () => {
    expect(
      problemsOf(row({ spend: 50, clicks: 20, orders: 1, roas: 3.0 }), 3.33),
    ).toContain('under')
    expect(
      problemsOf(row({ spend: 50, clicks: 20, orders: 1, roas: 0.8 }), 3.33),
    ).toContain('bleeding')
  })
})

describe('review state', () => {
  it('locks a quarantined campaign to keep, and counts it as forced not edited', () => {
    // "I overrode the agent" must mean the reviewer changed something.
    const doc = parseReport(
      report({
        note: '> ⚠️ 数据不可信 — 请勿执行本活动的出价建议\n',
        targeting:
          '| widget red | Exact | 2.00 | 40 | 80.00 | 0 | 0.50 | 下调至 1.20 |\n',
      }),
    )
    const state = buildReviewState(doc)
    const choices = initialChoices(state)
    const c = state.campaigns[0]
    const k = rowKey(c.id, c.kw[0])
    expect(choices.get(k)).toBe('keep')
    expect(state.forced).toBeGreaterThan(0)
    expect(totalsOf(state, choices).edited).toBe(0)
  })

  it('locks a bid trim whose CPC could not be computed', () => {
    const doc = parseReport(
      report({
        targeting:
          '| widget red | Exact | 2.00 | 0 | 80.00 | 0 | — | 下调至 0.05 |\n',
      }),
    )
    const state = buildReviewState(doc)
    const c = state.campaigns[0]
    expect(c.kw[0].bidUnsafe).toBe(true)
    expect(initialChoices(state).get(rowKey(c.id, c.kw[0]))).toBe('keep')
  })

  it('carries the match type when switching add <-> negate', () => {
    const state = buildReviewState(parseReport(report()))
    const c = state.campaigns[0]
    const k = rowKey(c.id, c.st[0])
    let ch = initialChoices(state)
    ch = chooseAction(ch, k, 'add')
    expect(ch.get(k)).toBe('add:exact')
    ch = chooseMatchType(ch, k, 'phrase')
    expect(ch.get(k)).toBe('add:phrase')
    ch = chooseAction(ch, k, 'neg')
    // The reviewer already answered "how broadly" — don't ask twice.
    expect(ch.get(k)).toBe('neg:phrase')
    ch = chooseAction(ch, k, 'keep')
    expect(ch.get(k)).toBe('keep')
  })

  it('never returns the same map, so React can see the change', () => {
    const state = buildReviewState(parseReport(report()))
    const c = state.campaigns[0]
    const before = initialChoices(state)
    const after = chooseAction(before, rowKey(c.id, c.st[0]), 'neg')
    expect(after).not.toBe(before)
    // and the previous map is untouched
    expect(before.get(rowKey(c.id, c.st[0]))).toBe('keep')
  })

  it('restoring the agent advice does NOT undo a safety lock', () => {
    const doc = parseReport(
      report({
        note: '> ⚠️ 数据不可信 — 请勿执行本活动的出价建议\n',
        targeting:
          '| widget red | Exact | 2.00 | 40 | 80.00 | 0 | 0.50 | 下调至 1.20 |\n',
      }),
    )
    const state = buildReviewState(doc)
    const c = state.campaigns[0]
    const ch = bulkSet(initialChoices(state), c, 'kw', 'sugg')
    expect(ch.get(rowKey(c.id, c.kw[0]))).toBe('keep')
  })
})

describe('buildDecisionSet', () => {
  it('omits rows that agree with the audit and do nothing', () => {
    const state = buildReviewState(parseReport(report()))
    expect(buildDecisionSet(state, initialChoices(state))).toEqual([])
  })

  it('carries the code, a stable label, and the match-type provenance', () => {
    const state = buildReviewState(parseReport(report()))
    const c = state.campaigns[0]
    const ch = chooseAction(initialChoices(state), rowKey(c.id, c.st[0]), 'neg')
    const set = buildDecisionSet(state, ch)
    const row = set[0].campaigns[0].rows[0]
    expect(row.action).toBe('neg')
    // Not localised: the executor must read the same verb whichever language
    // the reviewer happened to be using.
    expect(row.action_label).toBe('否定关键词')
    expect(row.match_type).toBe('exact')
    // The audit said 维持 here, so the match type is the page's fallback.
    expect(row.match_type_source).toBe('page_default')
    expect(row.overridden).toBe(true)
  })

  it('marks a match type the audit itself named as audit-sourced', () => {
    const state = buildReviewState(
      parseReport(
        report({
          searchTerms:
            '| red widget | widget red | Broad | 30 | 60.00 | 0 | — | 否定词组（零转化） |\n',
        }),
      ),
    )
    const set = buildDecisionSet(state, initialChoices(state))
    const row = set[0].campaigns[0].rows[0]
    expect(row.match_type).toBe('phrase')
    expect(row.match_type_source).toBe('audit')
    expect(row.overridden).toBe(false)
  })
})
