// The console appears because the phase SAID it was an audit, and shows
// only what that phase said it was about.
//
// Both facts used to be guessed from the agent's own markdown: a
// `## <platform> <CC>` heading plus a `drilled N/N` line. A task asked to
// create two campaigns for one product wrote one such heading and was
// handed a console listing every campaign in the store, most of it
// transcribed from an audit four days old.

import { describe, it, expect } from 'vitest'
import {
  declarationFor,
  droppedCampaigns,
  isWholeStore,
  narrowToScope,
  opensConsole,
  type AdDeclaration,
} from '../lib/adAudit/declaration'
import { parseReport } from '../lib/adAudit/parseReport'

function decl(
  seq: number,
  kind: AdDeclaration['kind'],
  scope: AdDeclaration['scope'],
  created_at: string,
): AdDeclaration {
  return { seq, kind, scope, created_at, user_turn: seq }
}

// Two marketplaces, three campaigns, one of which is the widget-006 family.
const REPORT = `# 广告优化建议 — acme — 2026-08-01

## amazon AE

**进度**: drilled 2/2 active (2 total, 1 page)

### A1234567 | widget-006 family manual | Manual

| 关键词 | 出价 | 点击 | 花费 | 订单 | ROAS | 建议 |
|---|---|---|---|---|---|---|
| cotton socks | 1.00 | 40 | 40.00 | 4 | 5.00 | 维持 |

### A7654321 | widget-006 manual | Manual

| 关键词 | 出价 | 点击 | 花费 | 订单 | ROAS | 建议 |
|---|---|---|---|---|---|---|
| widget red | 1.00 | 30 | 30.00 | 2 | 3.00 | 维持 |

## noon AE

**进度**: drilled 1/1 active (1 total, 1 page)

### C0000009 | unrelated auto | Auto

| 关键词 | 出价 | 点击 | 花费 | 订单 | ROAS | 建议 |
|---|---|---|---|---|---|---|
| something else | 0.80 | 10 | 8.00 | 0 | — | 暂停 |
`

const AMAZON_ONLY_SOCK = {
  combos: [{ platform: 'amazon', country: 'AE' }],
  campaigns: ['A1234567'],
  products: ['WIDGET-006'],
}

describe('which result opens a console', () => {
  it('opens for a phase that declared an audit', () => {
    expect(opensConsole(decl(1, 'audit', {}, '2026-08-01T10:00:00Z'))).toBe(
      true,
    )
  })

  it('does not open for a create phase', () => {
    // Creates are deliberately not console-gated: additive, capped by
    // daily budget, reversible with a pause, and already covered by the
    // reviewer loop.
    expect(opensConsole(decl(1, 'create', {}, '2026-08-01T10:00:00Z'))).toBe(
      false,
    )
  })

  it('does not open when nothing was declared', () => {
    expect(opensConsole(null)).toBe(false)
  })
})

describe('binding a result to its phase', () => {
  const created = decl(1, 'create', {}, '2026-08-01T10:00:00Z')
  const audited = decl(2, 'audit', AMAZON_ONLY_SOCK, '2026-08-01T12:00:00Z')

  it('gives an early result the phase that was current then', () => {
    // "Create a listing" → result → "now audit its ads" → result.
    // The first result must not sprout a console retroactively.
    const d = declarationFor([created, audited], '2026-08-01T11:00:00Z')
    expect(d?.kind).toBe('create')
    expect(opensConsole(d)).toBe(false)
  })

  it('gives a later result the newer phase', () => {
    const d = declarationFor([created, audited], '2026-08-01T13:00:00Z')
    expect(d?.kind).toBe('audit')
    expect(opensConsole(d)).toBe(true)
  })

  it('falls back to the latest when the timestamp is synthetic', () => {
    // Rebuilt history collapses to one result item stamped at rebuild
    // time, so "latest" is the right answer there.
    expect(declarationFor([created, audited], undefined)?.seq).toBe(2)
    expect(declarationFor([created, audited], 'not-a-date')?.seq).toBe(2)
  })

  it('a result produced BEFORE any declaration gets none', () => {
    // Turn 1 answers "how much did we sell today?" and finishes before
    // the ad-audit turn exists. Falling back to the latest declaration
    // here painted a second console onto that answer.
    expect(declarationFor([created, audited], '2026-08-01T09:00:00Z')).toBeNull()
    expect(
      opensConsole(declarationFor([audited], '2026-08-01T09:00:00Z')),
    ).toBe(false)
  })

  it('returns null when the task never declared anything', () => {
    expect(declarationFor([], '2026-08-01T13:00:00Z')).toBeNull()
    expect(declarationFor(undefined, '2026-08-01T13:00:00Z')).toBeNull()
  })
})

describe('narrowing the report to the declared scope', () => {
  const full = parseReport(REPORT)

  it('the report really does carry everything', () => {
    // Guard against the assertions below passing because the fixture
    // parsed to nothing.
    expect(full.sections).toHaveLength(2)
    expect(full.sections.flatMap((s) => s.campaigns)).toHaveLength(3)
  })

  it('keeps only the declared campaign, on the declared marketplace', () => {
    const d = decl(1, 'audit', AMAZON_ONLY_SOCK, '2026-08-01T12:00:00Z')
    const narrowed = narrowToScope(full, d)
    expect(narrowed.sections).toHaveLength(1)
    expect(narrowed.sections[0].platform.toLowerCase()).toBe('amazon')
    const kept = narrowed.sections.flatMap((s) => s.campaigns)
    expect(kept).toHaveLength(1)
    expect(kept[0].id).toBe('A1234567')
  })

  it('reports how many campaigns it hid', () => {
    const d = decl(1, 'audit', AMAZON_ONLY_SOCK, '2026-08-01T12:00:00Z')
    expect(droppedCampaigns(full, narrowToScope(full, d))).toBe(2)
  })

  it('drops a whole marketplace the phase never claimed', () => {
    const d = decl(
      1,
      'audit',
      { combos: [{ platform: 'amazon', country: 'AE' }] },
      '2026-08-01T12:00:00Z',
    )
    const narrowed = narrowToScope(full, d)
    expect(
      narrowed.sections.some((s) => s.platform.toLowerCase() === 'noon'),
    ).toBe(false)
    // …but keeps both amazon campaigns, since no campaign list narrowed it.
    expect(narrowed.sections.flatMap((s) => s.campaigns)).toHaveLength(2)
  })

  it('a whole-store audit is not narrowed at all', () => {
    const d = decl(1, 'audit', {}, '2026-08-01T12:00:00Z')
    expect(narrowToScope(full, d)).toEqual(full)
    expect(droppedCampaigns(full, narrowToScope(full, d))).toBe(0)
  })

  it('an undeclared report is left alone', () => {
    expect(narrowToScope(full, null)).toEqual(full)
  })

  it('absent combos means the whole store', () => {
    expect(isWholeStore({})).toBe(true)
    expect(isWholeStore(undefined)).toBe(true)
    expect(isWholeStore({ combos: [] })).toBe(true)
    expect(isWholeStore(AMAZON_ONLY_SOCK)).toBe(false)
  })
})

describe('a console with nothing to decide', () => {
  it('the parser really does yield nothing for an unsectioned report', () => {
    // What a real agent produced against a stub console that is not a
    // configured marketplace: a readable report, but with no
    // `## <platform> <CC>` section for the parser to key on.
    const plain = `# Bid Audit — widget-006 manual (A1234567)

Live · Daily budget 20.00 · Spend 200 / Revenue 600 / ROAS 3.00

| Keyword | Match | Bid | Clicks | Spend | ROAS |
|---|---|---|---|---|---|
| widget red | Exact | 1.00 | 60 | 120.00 | 3.00 |

**No bid changes recommended this week.**
`
    const doc = parseReport(plain)
    const rows = doc.sections.reduce((n, s) => n + s.campaigns.length, 0)
    expect(rows).toBe(0)
  })

  it('an audit declaration alone is not enough to show a console', () => {
    // opensConsole() is still true — the phase DID declare an audit —
    // so the emptiness has to be caught where the rows are counted, not
    // by second-guessing the declaration.
    const audited = decl(1, 'audit', {}, '2026-08-01T12:00:00Z')
    expect(opensConsole(audited)).toBe(true)
    const empty = parseReport('# Just prose, no sections\n\nnothing here.\n')
    expect(empty.sections.flatMap((s) => s.campaigns)).toHaveLength(0)
  })
})
