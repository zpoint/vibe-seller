// The scope controls are only meaningful if what leaves the page matches
// what the reviewer left in. These drive the real UI and inspect the
// submitted payload.

import { describe, expect, it } from 'vitest'
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react'
import i18n from 'i18next'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import en from '../i18n/locales/en/translation.json'
import zh from '../i18n/locales/zh/translation.json'
import { AuditConsole } from '../components/adAudit/AuditConsole'
import { parseReport } from '../lib/adAudit/parseReport'
import type { DecisionSubmission } from '../lib/adAudit/types'

function section(platform: string, country: string, ids: string[]) {
  const rows = ids
    .map((id) => `| ${id} | acme ${id} | Auto | 120.00 | 400.00 | 20 | 3.33 |`)
    .join('\n')
  const blocks = ids
    .map(
      (id) => `
### ${id} | acme ${id} | Auto | AG: acme group

| 定向词 | 匹配 | 出价 | 点击 | 花费 (SAR) | 订单 | ROAS | 建议 |
|---|---|---|---|---|---|---|---|
| widget red | Exact | 2.00 | 40 | 80.00 | 4 | 5.00 | 暂停 |
`,
    )
    .join('')
  return `## ${platform} ${country}
**进度**: drilled ${ids.length}/${ids.length} active (${ids.length} total, 1 pages)

| id | name | type | spend (SAR) | sales | orders | roas |
|---|---|---|---|---|---|---|
${rows}
${blocks}`
}

const WIDE =
  '# Ad audit\n\n' +
  section('amazon', 'SA', ['100000000001']) +
  section('noon', 'SA', ['C_SA1']) +
  section('amazon', 'AE', ['100000000003']) +
  `
## 汇总建议

| 平台 | 市场 | 活动数 | 花费 | 销售额 | 订单 | ROAS |
|---|---|---|---|---|---|---|
| amazon | SA | 1 | SAR 120.00 | 400.00 | 20 | 3.33 |

盈亏线 ROAS = 3.33
`

const SINGLE =
  '# Ad audit\n\n' +
  section('amazon', 'SA', ['100000000001']) +
  `
## 汇总建议

| 平台 | 市场 | 活动数 | 花费 | 销售额 | 订单 | ROAS |
|---|---|---|---|---|---|---|
| amazon | SA | 1 | SAR 120.00 | 400.00 | 20 | 3.33 |

盈亏线 ROAS = 3.33
`

async function mount(md: string, lang: 'en' | 'zh' = 'en') {
  const inst = i18n.createInstance()
  await inst.use(initReactI18next).init({
    lng: lang,
    fallbackLng: 'en',
    resources: { en: { translation: en }, zh: { translation: zh } },
    interpolation: { escapeValue: false },
  })
  let submitted: DecisionSubmission | null = null
  render(
    <I18nextProvider i18n={inst}>
      <AuditConsole doc={parseReport(md)} onSubmit={(d) => (submitted = d)} />
    </I18nextProvider>,
  )
  return { get submitted() { return submitted } }
}


/** Campaign blocks start collapsed; the row controls only exist once open. */
function expandFirstCampaign() {
  fireEvent.click(screen.getAllByRole('button', { expanded: false })[0])
}

const submitBtn = () =>
  screen.getByRole('button', { name: /Hand to agent|交给 agent/ })

describe('scope controls', () => {
  it('submits the whole report when nothing is dropped', async () => {
    const h = await mount(WIDE)
    fireEvent.click(submitBtn())
    expect(h.submitted).not.toBeNull()
    const ids = h.submitted!.markets.flatMap((m) => m.campaigns.map((c) => c.campaign_id))
    expect(ids.sort()).toEqual(['100000000001', '100000000003', 'C_SA1'])
    cleanup()
  })

  it('dropping a market removes its campaigns from the payload', async () => {
    const h = await mount(WIDE)
    // The SA section header carries the market-level drop.
    const sa = screen.getAllByRole('heading', { level: 3 }).find(
      (el) => el.textContent?.startsWith('SA'),
    )!
    fireEvent.click(within(sa).getByRole('button', { name: 'Drop' }))
    fireEvent.click(submitBtn())
    const countries = h.submitted!.markets.map((m) => m.country)
    expect(countries).toEqual(['AE'])
    cleanup()
  })

  it('"Only this one" leaves exactly one campaign', async () => {
    const h = await mount(WIDE)
    fireEvent.click(screen.getAllByRole('button', { name: 'Only this one' })[1])
    fireEvent.click(submitBtn())
    const ids = h.submitted!.markets.flatMap((m) => m.campaigns.map((c) => c.campaign_id))
    expect(ids).toEqual(['C_SA1'])
    cleanup()
  })

  it('a dropped block stays visible so the reviewer can see what they excluded', async () => {
    await mount(WIDE)
    fireEvent.click(screen.getAllByRole('button', { name: 'Drop' })[0])
    // The market shows its own undo; the campaigns under it say they were
    // excluded WITH the market and offer no dead toggle of their own.
    expect(screen.getAllByRole('button', { name: /Dropped/ })).toHaveLength(1)
    const inherited = screen.getAllByRole('button', {
      name: 'Excluded with its market',
    })
    expect(inherited.length).toBeGreaterThan(0)
    expect(inherited.every((b) => (b as HTMLButtonElement).disabled)).toBe(true)
    cleanup()
  })

  it('reports the narrowing, and can undo it', async () => {
    await mount(WIDE)
    fireEvent.click(screen.getAllByRole('button', { name: 'Only this one' })[0])
    expect(screen.getByText(/Submitting 1 of 3 campaigns/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Include everything' }))
    expect(screen.queryByText(/Submitting 1 of 3/)).toBeNull()
    cleanup()
  })

  it('works the same on a single-market report', async () => {
    const h = await mount(SINGLE)
    fireEvent.click(submitBtn())
    expect(h.submitted!.markets.flatMap((m) => m.campaigns)).toHaveLength(1)
    cleanup()
  })

  it('is translated', async () => {
    await mount(WIDE, 'zh')
    expect(screen.getAllByRole('button', { name: '不执行' }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('button', { name: '只留这个' }).length).toBeGreaterThan(0)
    cleanup()
  })
})

describe('a bid move must say how much', () => {
  it('blocks submit until the reviewer sets an amount', async () => {
    // Found live: overriding a 维持 into a raise sent
    // `action: raise, target_bid: null` — "raise it" with no number,
    // which is not an instruction anyone can carry out.
    const h = await mount(WIDE)
    expandFirstCampaign()
    fireEvent.click(screen.getAllByRole('button', { name: 'Raise bid' })[0])
    expect(screen.getByText(/set a bid on 1 row/)).toBeTruthy()
    expect((submitBtn() as HTMLButtonElement).disabled).toBe(true)

    const input = screen.getByLabelText('New bid')
    fireEvent.change(input, { target: { value: '2.90' } })
    expect((submitBtn() as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(submitBtn())
    const row = h.submitted!.markets[0].campaigns[0].rows.find(
      (r) => r.action === 'raise',
    )!
    expect(row.target_bid).toBe(2.9)
    expect(h.submitted!.totals.rows_missing_bid).toBe(0)
    cleanup()
  })

  it('keeps the audit’s own number when it proposed one', async () => {
    await mount(WIDE)
    expandFirstCampaign()
    fireEvent.click(screen.getAllByRole('button', { name: 'Raise bid' })[0])
    // Nothing typed, and this fixture's advice is 暂停 (no target), so the
    // guard is what stops it — not a silently-invented number.
    expect(screen.getByText(/set a bid on 1 row/)).toBeTruthy()
    cleanup()
  })

  it('clearing the field re-blocks submission', async () => {
    await mount(WIDE)
    expandFirstCampaign()
    fireEvent.click(screen.getAllByRole('button', { name: 'Raise bid' })[0])
    const input = screen.getByLabelText('New bid')
    fireEvent.change(input, { target: { value: '2.90' } })
    expect((submitBtn() as HTMLButtonElement).disabled).toBe(false)
    fireEvent.change(input, { target: { value: '' } })
    expect((submitBtn() as HTMLButtonElement).disabled).toBe(true)
    cleanup()
  })
})

describe('a bid move that changes nothing is not an instruction', () => {
  it('blocks submit when the new bid equals the current one', async () => {
    // Live: the report's table was left at the pre-change value, so the
    // reviewer typed what they could see and the console emitted
    // `lower 2.8 -> 2.8` — a no-op the executor cannot act on.
    await mount(WIDE)
    expandFirstCampaign()
    fireEvent.click(screen.getAllByRole('button', { name: 'Raise bid' })[0])
    const input = screen.getByLabelText('New bid')
    // the fixture's row carries bid 2.00
    fireEvent.change(input, { target: { value: '2.00' } })
    expect(screen.getByText(/set to the same value as now/)).toBeTruthy()
    expect((submitBtn() as HTMLButtonElement).disabled).toBe(true)
    // a real move unblocks it
    fireEvent.change(input, { target: { value: '2.20' } })
    expect((submitBtn() as HTMLButtonElement).disabled).toBe(false)
    cleanup()
  })
})
