// Parse an ad-audit report into the structure the console renders.
//
// The guiding rule: READ the audit's recommendation, never infer one. Where
// a recommendation does not name an executable action the row is reported as
// AMBIGUOUS and nothing is pre-selected. An earlier version silently
// defaulted a bare 否定 to 否定精确, which made the PAGE the thing deciding
// phrase-vs-exact negation on real money — a decision that belongs to the
// audit. The one sanctioned fallback (match type → 精准) is recorded as
// `mtFromAudit: false` so it can be shown as the page's choice, not the
// audit's.

import type {
  AuditCampaign,
  AuditDoc,
  AuditRow,
  AuditSection,
  AuditSummary,
  Layer,
  MatchType,
  MarketRollup,
  Reconciliation,
} from './types'
import { DEFAULT_MT, isProductTarget, joinAct } from './vocabulary'

// Platform + marketplace heading. Amazon runs 20+ marketplaces, so the
// country is matched by SHAPE (two or three capitals) rather than against a
// hardcoded list — three such lists had already drifted out of sync, and a
// missing code silently dropped a whole market from the page.
const COMBO_RE = /^##\s+(amazon|noon)\s+([A-Z]{2,3})\b/i
const CUR_RE = /\((SAR|AED|A\$|USD|EGP|MXN|£|€)\)/

interface RawTable {
  head: string[]
  rows: string[][]
}

function cellsOf(row: string): string[] {
  return row
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim())
}

/** Every pipe table in a chunk of markdown, in document order. */
export function splitTables(text: string): RawTable[] {
  const blocks: string[][] = []
  let cur: string[] | null = null
  for (const line of text.split('\n')) {
    if (line.trim().startsWith('|')) {
      if (!cur) cur = []
      cur.push(line)
    } else if (cur) {
      blocks.push(cur)
      cur = null
    }
  }
  if (cur) blocks.push(cur)
  const out: RawTable[] = []
  for (const block of blocks) {
    const body = block.filter((r) => !/^[\s|:-]+$/.test(r))
    if (body.length) {
      out.push({ head: cellsOf(body[0]), rows: body.slice(1).map(cellsOf) })
    }
  }
  return out
}

/**
 * A figure from a report cell. An em dash, '∞' or a blank is absent data,
 * NOT zero — a zero would silently enter averages and rollups.
 */
export function num(s: string | null | undefined): number | null {
  if (s == null) return null
  const t = String(s).replace(/,/g, '')
  if (/^[—\-–\s]*$/.test(t) || t === '∞' || t === '') return null
  const m = t.match(/-?\d+(\.\d+)?/)
  return m ? parseFloat(m[0]) : null
}

interface Suggestion {
  act: AuditRow['sugg'] extends null ? never : string | null
  mt?: MatchType | null
  mtFromAudit?: boolean
  to?: number
  ambiguous?: string
}

/** Read an action out of the audit's 建议 cell. */
export function suggest(
  advice: string | null | undefined,
  layer: Layer,
  match?: string | null,
): Suggestion {
  const a = advice || ''
  if (layer === 'st') {
    if (isProductTarget(match)) {
      // No match-type variants exist here — 否定 IS the whole action.
      if (/否定|排除/.test(a)) return { act: 'negt' }
      if (/维持/.test(a)) return { act: 'keep' }
      return { act: null, ambiguous: a ? '建议无法对应到动作' : '没有建议' }
    }
    const mt: MatchType | null = /精确|精准|\bexact\b/i.test(a)
      ? 'exact'
      : /词组|\bphrase\b/i.test(a)
        ? 'phrase'
        : null
    if (/拓词|添加为?关键词|提取/.test(a)) {
      return { act: 'add', mt: mt || DEFAULT_MT, mtFromAudit: mt != null }
    }
    if (/维持/.test(a)) return { act: 'keep' }
    if (/否定|排除/.test(a)) {
      return { act: 'neg', mt: mt || DEFAULT_MT, mtFromAudit: mt != null }
    }
    return { act: null, ambiguous: a ? '建议无法对应到动作' : '没有建议' }
  }
  let m = a.match(/提高至\s*([\d.]+)/)
  if (m) return { act: 'raise', to: parseFloat(m[1]) }
  m = a.match(/(?:下调至|降至|下调到)\s*([\d.]+)/)
  if (m) return { act: 'lower', to: parseFloat(m[1]) }
  if (/暂停/.test(a)) return { act: 'pause' }
  if (/维持/.test(a)) return { act: 'keep' }
  // 否定 on a TARGET is a pause: you cannot negate something you bid on.
  if (/否定/.test(a)) return { act: 'pause' }
  return { act: null, ambiguous: a ? '建议无法对应到动作' : '没有建议' }
}

function buildRows(table: RawTable | null, layer: Layer): AuditRow[] {
  if (!table) return []
  const ix = (re: RegExp) => table.head.findIndex((h) => re.test(h))
  const iSrc = ix(/来源/)
  const iM = ix(/匹配/)
  const iB = ix(/出价/)
  const iC = ix(/点击/)
  const iSp = ix(/花费/)
  const iO = ix(/订单/)
  const iSa = ix(/销售/)
  const iA = ix(/acos/i)
  const iR = ix(/roas/i)
  const iAd = ix(/建议/)
  const rows: AuditRow[] = []
  for (const r of table.rows) {
    const term = (r[0] || '').replace(/\*\*/g, '').trim()
    if (!term) continue
    if (/^(合计|总计|汇总|整体活动|定位层汇总)/.test(term)) continue
    const advice = iAd >= 0 ? r[iAd] || '' : ''
    const sg = suggest(advice, layer, iM >= 0 ? r[iM] : '')
    const row: AuditRow = {
      layer,
      uid: `${layer}#${rows.length}`,
      term,
      src: iSrc >= 0 ? r[iSrc] || '' : '',
      match: iM >= 0 ? r[iM] || '' : '',
      bid: iB >= 0 ? num(r[iB]) : null,
      clicks: iC >= 0 ? num(r[iC]) : null,
      spend: iSp >= 0 ? num(r[iSp]) : null,
      orders: iO >= 0 ? num(r[iO]) : null,
      sales: iSa >= 0 ? num(r[iSa]) : null,
      acos: iA >= 0 ? r[iA] || '' : '',
      roas: iR >= 0 ? num(r[iR]) : null,
      advice,
      sugg:
        sg.act == null
          ? null
          : joinAct(sg.act as never, sg.mt ?? null),
      mtFromAudit: sg.mtFromAudit ?? null,
      ambiguous: sg.ambiguous || null,
      to: sg.to ?? null,
    }
    // Spend with ZERO clicks cannot happen on CPC billing, so any figure
    // derived from CPC on that row came from dividing by nothing. Only SOME
    // advice depends on CPC, and blocking all of it threw away valid work:
    //   * LOWER is floored at CPC×1.1, so its target is garbage — such rows
    //     printed a floor near zero against a bid orders of magnitude above
    //     it.
    //   * RAISE triggers on ROAS = sales/spend; both columns rendered, so it
    //     stands.
    //   * pause / negate / add never touch CPC, so they stand.
    if ((row.spend || 0) > 0 && row.clicks === 0) {
      row.flag = 'no_clicks'
      row.bidUnsafe = sg.act === 'lower'
    }
    rows.push(row)
  }
  // Search terms in spend order: the money order is the review order. The
  // report's own top-N slice is already sorted, but a TSV-derived tail is
  // not guaranteed to be.
  if (layer === 'st') rows.sort((a, b) => (b.spend || 0) - (a.spend || 0))
  return rows
}

function parseCampaign(
  block: string,
  rollup: Record<string, Partial<AuditCampaign>>,
  platform: string,
  country: string,
): AuditCampaign | null {
  const bits = (block.split('\n')[0] || '')
    .replace(/^###\s*/, '')
    .split('|')
    .map((s) => s.trim())
  const id = bits[0]
  if (!id || /^已下线|^未投入/.test(id)) return null
  const meta = rollup[id] || {}

  let quarantine: string | null = null
  for (const ln of block.split('\n')) {
    if (/数据不可信|数据矛盾/.test(ln) && /请勿执行|不要执行/.test(ln)) {
      quarantine = ln.replace(/^[>\s]*⚠️?\s*/, '').trim()
      break
    }
  }

  const rc = block.match(
    /搜索词对账[^\n]*?定向花费\s*\S*\s*([\d,.]+)\s*\/\s*点击\s*([\d,]+)[^\n]*?搜索词花费\s*\S*\s*([\d,.]+)\s*\/\s*点击\s*([\d,]+)\s*\((✓|✗|x|X)\)/,
  )
  const recon: Reconciliation | null = rc
    ? {
        tSpend: num(rc[1]),
        tClicks: num(rc[2]),
        sSpend: num(rc[3]),
        sClicks: num(rc[4]),
        ok: rc[5] === '✓',
      }
    : null

  let tgt: RawTable | null = null
  let st: RawTable | null = null
  let currency = ''
  for (const t of splitTables(block)) {
    const h0 = t.head[0] || ''
    const cm = (t.head.join(' ').match(CUR_RE) || [])[1]
    if (cm && !currency) currency = cm
    if (/关键词|定向/.test(h0) && !tgt) tgt = t
    else if (/搜索词|查询|search|customer/i.test(h0) && !st) st = t
  }

  // The ad group lives in a trailing heading field: `AG: <name>`, or
  // `AGs: <a>, <b>` when a campaign has several (one live campaign had 3).
  // Amazon groups by ad group, noon by SKU; the report labels whichever
  // applies, so render what it wrote.
  let adGroups: string[] = []
  for (const f of bits.slice(2)) {
    const m = f.match(/^AGs?\s*[:：]\s*(.+)$/i)
    if (m) {
      adGroups = m[1]
        .split(/[,，]/)
        .map((x) => x.trim())
        .filter(Boolean)
    }
  }

  const fallbackCurrency: Record<string, string> = {
    SA: 'SAR',
    AE: 'AED',
    AU: 'A$',
  }
  return {
    id,
    name: bits[1] && bits[1] !== id ? bits[1] : meta.name || id,
    type:
      bits[2] && !/^AGs?\s*[:：]/i.test(bits[2]) ? bits[2] : meta.type || '',
    adGroups,
    platform,
    country,
    currency: currency || fallbackCurrency[country] || '',
    spend: meta.spend ?? null,
    sales: meta.sales ?? null,
    orders: meta.orders ?? null,
    roas: meta.roas ?? null,
    quarantine,
    recon,
    kw: buildRows(tgt, 'kw'),
    st: buildRows(st, 'st'),
  }
}

export function parseSummary(text: string | null): AuditSummary {
  if (!text) return { markets: [], breakeven: null }
  const t = splitTables(text).find((x) => /平台|platform/i.test(x.head[0] || ''))
  const markets: MarketRollup[] = []
  if (t) {
    for (const r of t.rows) {
      const p = (r[0] || '').replace(/\*/g, '').trim()
      if (!/^(amazon|noon)$/i.test(p)) continue
      markets.push({
        platform: p.toLowerCase(),
        country: (r[1] || '').toUpperCase(),
        n: num(r[2]),
        spend: num(r[3]),
        sales: num(r[4]),
        orders: num(r[5]),
        roas: num(r[6]),
        currency:
          ((r[3] || '').match(/(SAR|AED|A\$|USD|EGP|MXN)/) || [])[1] || '',
      })
    }
  }
  const be = text.match(/盈亏线\s*ROAS\s*=\s*([\d.]+)/)
  return { markets, breakeven: be ? parseFloat(be[1]) : null }
}

export function parseReport(md: string): AuditDoc {
  const sections: AuditSection[] = []
  let summary: string | null = null
  for (const part of md.split(/^(?=## )/m)) {
    const first = (part.split('\n')[0] || '').trim()
    const m = first.match(COMBO_RE)
    if (!m) {
      if (/^##\s*汇总建议/.test(first)) summary = part
      continue
    }
    const platform = m[1].toLowerCase()
    const country = m[2].toUpperCase()
    const prog = part.match(/drilled\s+(\d+)\/(\d+)\s+active/)

    // The combo table carries per-campaign rollups keyed by id, so a
    // campaign block that omits its own totals still gets them.
    const tables = splitTables(part)
    const combo = tables.find((t) => /^(id|活动\s*id)$/i.test(t.head[0] || ''))
    const rollup: Record<string, Partial<AuditCampaign>> = {}
    if (combo) {
      const ix = (re: RegExp) => combo.head.findIndex((h) => re.test(h))
      const iS = ix(/spend|花费/)
      const iSa = ix(/sales|销售/)
      const iO = ix(/orders|订单/)
      const iR = ix(/roas/i)
      const iN = ix(/name|名称/)
      const iT = ix(/type|类型/)
      for (const r of combo.rows) {
        const id = (r[0] || '').replace(/\*/g, '').trim()
        if (!id || /总计|合计/.test(id)) continue
        rollup[id] = {
          name: (r[iN] || id).trim(),
          type: (r[iT] || '').trim(),
          spend: num(r[iS]),
          sales: num(r[iSa]),
          orders: num(r[iO]),
          roas: num(r[iR]),
        }
      }
    }

    const campaigns: AuditCampaign[] = []
    for (const b of part.split(/^(?=### )/m).slice(1)) {
      const c = parseCampaign(b, rollup, platform, country)
      if (c) campaigns.push(c)
    }
    sections.push({
      platform,
      country,
      label: `${m[1]} ${country}`,
      drilled: prog ? +prog[1] : null,
      active: prog ? +prog[2] : null,
      campaigns,
    })
  }
  return { sections, summary: parseSummary(summary) }
}

