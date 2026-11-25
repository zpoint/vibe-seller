/**
 * A multi-select question takes several picks, plus typed text, sent
 * as one answer joined the way Claude Code's AskUserQuestion joins them.
 */
import { describe, it, expect, vi } from 'vitest'
import { createRef, useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { QuestionBanner } from '../components/QuestionBanner'
import { i18nTestInstance } from '../test/helpers'

const QS = [
  {
    header: 'Issues',
    question: 'What looked off?',
    multiSelect: true,
    options: [{ label: 'Layout' }, { label: 'Colors' }, { label: 'Text' }],
  },
  { header: 'Again', question: 'Try again?', options: [{ label: 'Yes' }, { label: 'No' }] },
]

function Harness({ onSubmit }: { onSubmit: (a: Record<string, string>) => void }) {
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const set = (q: string, a: string) =>
    setAnswers(p => { const n = { ...p }; if (a) n[q] = a; else delete n[q]; return n })
  return (
    <I18nextProvider i18n={i18nTestInstance}>
      <QuestionBanner
        bannerRef={createRef()}
        questions={QS}
        selectedAnswers={answers}
        showOtherInput={{}}
        otherInputs={{}}
        onSelectAnswer={(q, a) => set(q, a)}
        onSetAnswer={set}
        onToggleOther={() => {}}
        onSetOtherAnswer={() => {}}
        onSubmitAll={o => onSubmit(o ?? answers)}
      />
    </I18nextProvider>
  )
}

const btn = (name: string) => screen.getByRole('button', { name })
const submit = () => screen.getByRole('button', { name: /Submit/ })

describe('multi-select question', () => {
  it('says it takes several picks, and only that question does', () => {
    render(<Harness onSubmit={() => {}} />)
    expect(document.querySelectorAll('[data-multi-select-hint]')).toHaveLength(1)
  })

  it('sends every pick and the typed text as one answer', () => {
    const onSubmit = vi.fn()
    render(<Harness onSubmit={onSubmit} />)
    fireEvent.click(btn('Layout'))
    fireEvent.click(btn('Text'))
    fireEvent.click(screen.getAllByRole('button', { name: /Other/ })[0])
    fireEvent.change(screen.getByPlaceholderText(/answer/i), { target: { value: 'icons too big' } })
    fireEvent.click(btn('No'))
    expect(btn('Layout')).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(submit())
    expect(onSubmit).toHaveBeenCalledWith({
      'What looked off?': 'Layout, Text, icons too big',
      'Try again?': 'No',
    })
  })

  it('unpicking everything leaves the question unanswered', () => {
    render(<Harness onSubmit={() => {}} />)
    fireEvent.click(btn('No'))
    fireEvent.click(btn('Colors'))
    expect(submit()).toBeEnabled()
    fireEvent.click(btn('Colors'))
    expect(submit()).toBeDisabled()
  })

  it('a single-select question still takes one pick', () => {
    const onSubmit = vi.fn()
    render(<Harness onSubmit={onSubmit} />)
    fireEvent.click(btn('Colors'))
    fireEvent.click(btn('Yes'))
    fireEvent.click(btn('No'))
    fireEvent.click(submit())
    expect(onSubmit).toHaveBeenCalledWith({ 'What looked off?': 'Colors', 'Try again?': 'No' })
  })
})
