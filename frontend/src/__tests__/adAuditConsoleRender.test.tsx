// The console is a bilingual surface, so nothing user-facing may be
// hardcoded. These render it under a real i18next instance in BOTH languages
// and assert the labels come from the locale files — a hardcoded string would
// show identical text in both and fail here.
//
// One label set is deliberately NOT localised: the decision file handed to
// the agent keeps the report's own vocabulary, because the executor must read
// the same verb whichever language the reviewer happened to be using.

import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import i18n from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { initReactI18next } from 'react-i18next'
import en from '../i18n/locales/en/translation.json'
import zh from '../i18n/locales/zh/translation.json'
import { AuditResultCard } from '../components/adAudit/AuditResultCard'
import { parseReport } from '../lib/adAudit/parseReport'
import {
  buildDecisionSet,
  buildReviewState,
  chooseAction,
  initialChoices,
  rowKey,
} from '../lib/adAudit/review'

const REPORT = `# Ad audit

## amazon SA
**进度**: drilled 1/1 active (1 total, 1 pages)

| id | name | type | spend (SAR) | sales | orders | roas |
|---|---|---|---|---|---|---|
| 100000000001 | acme-widget-006 auto | Auto | 120.00 | 400.00 | 20 | 3.33 |

### 100000000001 | acme-widget-006 auto | Auto | AG: acme-widget-006 group

| 定向词 | 匹配 | 出价 | 点击 | 花费 (SAR) | 订单 | ROAS | 建议 |
|---|---|---|---|---|---|---|---|
| widget red | Exact | 2.00 | 40 | 80.00 | 4 | 5.00 | 维持 |

| 搜索词 | 来源关键词 | 匹配 | 点击 | 花费 (SAR) | 订单 | ROAS | 建议 |
|---|---|---|---|---|---|---|---|
| red widget | widget red | Broad | 30 | 60.00 | 0 | — | 否定词组（零转化） |

## 汇总建议

| 平台 | 市场 | 活动数 | 花费 | 销售额 | 订单 | ROAS |
|---|---|---|---|---|---|---|
| amazon | SA | 1 | SAR 120.00 | 400.00 | 20 | 3.33 |

盈亏线 ROAS = 3.33
`

async function mount(lang: 'en' | 'zh') {
  const instance = i18n.createInstance()
  await instance.use(initReactI18next).init({
    lng: lang,
    fallbackLng: 'en',
    resources: { en: { translation: en }, zh: { translation: zh } },
    interpolation: { escapeValue: false },
  })
  // The card's open state is owned by the URL in the app; here a tiny
  // holder stands in for the router so the test drives it the same way.
  function Harness() {
    const [open, setOpen] = useState(false)
    return (
      <AuditResultCard
        report={REPORT}
        open={open}
        onOpen={() => setOpen(true)}
        onClose={() => setOpen(false)}
        onSubmit={() => {}}
      />
    )
  }
  return render(
    <I18nextProvider i18n={instance}>
      <Harness />
    </I18nextProvider>,
  )
}

describe('AuditResultCard', () => {
  it('summarises instead of dumping the report, in English', async () => {
    await mount('en')
    expect(screen.getByText(/1 markets · 1 live campaigns/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Open the decision console/ })).toBeTruthy()
    // The raw markdown must NOT be on the page.
    expect(screen.queryByText(/盈亏线/)).toBeNull()
    cleanup()
  })

  it('summarises in Chinese when that is the chosen language', async () => {
    await mount('zh')
    expect(screen.getByText(/1 个国家 · 1 个在投活动/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /打开决策台/ })).toBeTruthy()
    cleanup()
  })
})

describe('AuditConsole', () => {
  it('opens with English action buttons and the audit pre-selected', async () => {
    await mount('en')
    fireEvent.click(
      screen.getByRole('button', { name: /Open the decision console/ }),
    )
    // Expand the one campaign.
    fireEvent.click(screen.getByRole('button', { expanded: false }))

    // Targeting layer keeps the bid verbs; the search-term layer gets the
    // two-part vocabulary.
    expect(screen.getByRole('button', { name: 'Raise bid' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Add as keyword' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Negate keyword' })).toBeTruthy()

    // The audit said 否定词组, so negate is pre-selected at Phrase.
    const negate = screen.getByRole('button', { name: 'Negate keyword' })
    expect(negate.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'Phrase' }).getAttribute('aria-pressed')).toBe('true')
    cleanup()
  })

  it('shows the same controls in Chinese', async () => {
    await mount('zh')
    fireEvent.click(screen.getByRole('button', { name: /打开决策台/ }))
    fireEvent.click(screen.getByRole('button', { expanded: false }))
    expect(screen.getByRole('button', { name: '添加为关键词' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '否定关键词' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '词组' })).toBeTruthy()
    cleanup()
  })

  it('reveals the match-type row only for a match-type action', async () => {
    await mount('en')
    fireEvent.click(
      screen.getByRole('button', { name: /Open the decision console/ }),
    )
    fireEvent.click(screen.getByRole('button', { expanded: false }))
    expect(screen.queryByRole('button', { name: 'Exact' })).toBeTruthy()
    // Switching to Keep drops the qualifier — there is nothing to qualify.
    fireEvent.click(screen.getAllByRole('button', { name: 'Keep' })[1])
    expect(screen.queryByRole('button', { name: 'Exact' })).toBeNull()
    cleanup()
  })

  it('keeps the agent-facing labels unlocalised', () => {
    // A reviewer in English still hands the agent the report's vocabulary.
    const doc = parseReport(REPORT)
    const state = buildReviewState(doc)
    const c = state.campaigns[0]
    const ch = chooseAction(initialChoices(state), rowKey(c.id, c.st[0]), 'add')
    const row = buildDecisionSet(state, ch)[0].campaigns[0].rows[0]
    expect(row.action).toBe('add')
    expect(row.action_label).toBe('添加为关键词')
  })
})
