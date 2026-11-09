/**
 * The task footer's usage chip: context bar, cost by basis, breakdown.
 */
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { TaskUsageMeter } from '../components/TaskUsageMeter'
import { contextPercent, formatCost, formatTokens } from '../lib/usageFormat'
import type { TaskUsage } from '../types'

const i18nTestInstance = i18n.createInstance()
i18nTestInstance.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

const METERED: TaskUsage = {
  cost_usd: 1.2345,
  input_tokens: 12_000,
  output_tokens: 3_400,
  cache_read_tokens: 430_000,
  cache_write_tokens: 6_900,
  context_tokens: 452_300,
  context_window: 1_000_000,
  usage_model: 'MiniMax-M3.1-Flash',
  cost_basis: 'catalog',
  num_turns: 7,
}

function renderMeter(usage: TaskUsage) {
  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <TaskUsageMeter usage={usage} />
    </I18nextProvider>,
  )
}

describe('usage formatting', () => {
  it.each([
    [950, '950'],
    [1_000, '1k'],
    [45_300, '45.3k'],
    [452_300, '452k'],
    [1_000_000, '1M'],
    [1_048_576, '1M'],
    [2_450_000, '2.5M'],
  ])('formatTokens(%i) = %s', (n, out) => {
    expect(formatTokens(n)).toBe(out)
  })

  it.each([
    [0, '$0.00'],
    [0.004, '$0.0040'],
    [0.00684, '$0.0068'],
    [1.2345, '$1.23'],
    [250.4, '$250'],
  ])('formatCost(%f) = %s', (n, out) => {
    expect(formatCost(n)).toBe(out)
  })

  it('clamps the context percent and needs a window', () => {
    expect(contextPercent({ context_tokens: 452_300, context_window: 1_000_000 })).toBe(45)
    // Auto-compaction can briefly report past the window.
    expect(contextPercent({ context_tokens: 1_200_000, context_window: 1_000_000 })).toBe(100)
    expect(contextPercent({ context_tokens: 5_000, context_window: null })).toBeNull()
  })
})

describe('TaskUsageMeter', () => {
  it('renders nothing before the first metered call', () => {
    const { container } = renderMeter({})
    expect(container).toBeEmptyDOMElement()
  })

  it('shows context fill and an estimated cost for a priced run', () => {
    renderMeter(METERED)
    expect(screen.getByTestId('task-usage-context')).toHaveTextContent('45% · 452k / 1M')
    expect(screen.getByTestId('task-usage-cost')).toHaveTextContent('≈$1.23')
  })

  it('shows a sub-cent cost instead of hiding it', () => {
    renderMeter({ ...METERED, cost_usd: 0.00684 })
    expect(screen.getByTestId('task-usage-cost')).toHaveTextContent('≈$0.0068')
  })

  it('says "included" for a plan run instead of a $0 cost', () => {
    renderMeter({ ...METERED, cost_usd: 0, cost_basis: 'included' })
    expect(screen.getByTestId('task-usage-cost')).toHaveTextContent('Included in plan')
    expect(screen.queryByText(/\$0/)).toBeNull()
  })

  it('shows tokens but no price for an unpriced model', () => {
    renderMeter({ ...METERED, cost_basis: 'unpriced', cost_usd: 0 })
    expect(screen.queryByTestId('task-usage-cost')).toBeNull()
    fireEvent.click(screen.getByTestId('task-usage-meter'))
    expect(screen.getByTestId('task-usage-details')).toHaveTextContent('No price for this model')
  })

  it('falls back to a token count when the window is unknown', () => {
    renderMeter({ ...METERED, context_window: null })
    expect(screen.getByTestId('task-usage-context')).toHaveTextContent('Context 452k')
  })

  it('opens a breakdown with every bucket, and closes on outside click', () => {
    renderMeter(METERED)
    const button = screen.getByTestId('task-usage-meter')
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    const details = screen.getByTestId('task-usage-details')
    for (const text of [
      'MiniMax-M3.1-Flash',
      '452k / 1M (45%)',
      '12k', // input
      '430k', // cache read
      '6.9k', // cache write
      '3.4k', // output
      '7', // turns
      '≈$1.23',
      'models.dev',
    ]) {
      expect(details).toHaveTextContent(text)
    }
    fireEvent.mouseDown(document.body)
    expect(screen.queryByTestId('task-usage-details')).toBeNull()
  })
})

describe('translations', () => {
  it('has every usage key in both languages', () => {
    const en = Object.keys(enTranslation.usage).sort()
    const zh = Object.keys((zhTranslation as typeof enTranslation).usage).sort()
    expect(zh).toEqual(en)
  })
})
