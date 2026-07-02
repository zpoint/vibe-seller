import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { createRef } from 'react'

// The component calls useTranslation(); stub it so the test renders the
// REAL QuestionBanner without a full i18n provider.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

import { QuestionBanner } from '../components/QuestionBanner'

function renderReal(question: string) {
  const noop = () => {}
  return render(
    <QuestionBanner
      bannerRef={createRef<HTMLDivElement>()}
      questions={[{ header: 'H', question, options: [{ label: 'OK' }] }]}
      selectedAnswers={{}}
      showOtherInput={{}}
      otherInputs={{}}
      onSelectAnswer={noop}
      onToggleOther={noop}
      onSetOtherAnswer={noop}
      onSubmitAll={noop}
    />,
  )
}

describe('QuestionBanner markdown rendering', () => {
  it('preserves single-newline line breaks as <br> (the run-on-wall fix)', () => {
    const { container } = renderReal('line A\nline B\nline C')
    // Without preserveBreaks these collapse to one line (0 <br>).
    expect(container.querySelectorAll('br').length).toBeGreaterThanOrEqual(2)
    const text = container.textContent || ''
    expect(text).toContain('line A')
    expect(text).toContain('line C')
  })

  it('renders CommonMark bold + lists', () => {
    const { container } = renderReal('**bold text**\n\n- item one\n- item two')
    expect(container.querySelector('strong')?.textContent).toBe('bold text')
    expect(container.querySelectorAll('li').length).toBe(2)
  })

  it('renders GFM tables (remark-gfm)', () => {
    const { container } = renderReal('| C1 | C2 |\n|----|----|\n| a | b |')
    expect(container.querySelector('table')).not.toBeNull()
    expect(container.querySelectorAll('td').length).toBe(2)
  })
})
