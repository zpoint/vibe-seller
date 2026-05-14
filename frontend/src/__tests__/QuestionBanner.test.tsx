import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'

// Standalone test components that mirror App.tsx logic
// (we test the logic, not the full App component)

interface Question {
  header?: string
  question: string
  options?: { label: string; description?: string }[]
}

function QuestionBanner({
  questions,
  onSubmit,
}: {
  questions: Question[]
  onSubmit: (answers: Record<string, string>) => void
}) {
  const [selectedAnswers, setSelectedAnswers] = useState<Record<string, string>>({})
  const [showOtherInput, setShowOtherInput] = useState<Record<string, boolean>>({})
  const [otherInputs, setOtherInputs] = useState<Record<string, string>>({})
  const [freeTextMode, setFreeTextMode] = useState(false)
  const [freeTextInput, setFreeTextInput] = useState('')

  const selectAnswer = (q: string, a: string) => {
    setSelectedAnswers(prev => ({ ...prev, [q]: a }))
    setShowOtherInput(prev => ({ ...prev, [q]: false }))
    setOtherInputs(prev => ({ ...prev, [q]: '' }))
  }

  const toggleOther = (q: string) => {
    setShowOtherInput(prev => {
      const show = !prev[q]
      if (show) {
        // Opening Other → deselect predefined option
        setSelectedAnswers(p => { const n = { ...p }; delete n[q]; return n })
      } else {
        // Closing Other → clear input and answer
        setOtherInputs(p => ({ ...p, [q]: '' }))
        setSelectedAnswers(p => { const n = { ...p }; delete n[q]; return n })
      }
      return { ...prev, [q]: show }
    })
  }

  const setOtherAnswer = (q: string, text: string) => {
    setOtherInputs(prev => ({ ...prev, [q]: text }))
    if (text.trim()) {
      setSelectedAnswers(prev => ({ ...prev, [q]: text.trim() }))
    } else {
      setSelectedAnswers(prev => { const n = { ...prev }; delete n[q]; return n })
    }
  }

  const allAnswered = Object.keys(selectedAnswers).length >= questions.length
  const submitDisabled = freeTextMode ? !freeTextInput.trim() : !allAnswered

  return (
    <div data-testid="question-banner">
      <button
        data-testid="toggle-free-text"
        onClick={() => setFreeTextMode(prev => !prev)}
      >
        {freeTextMode ? 'Back to options' : 'Type freely instead'}
      </button>
      {!freeTextMode && questions.map((q, qi) => (
        <div key={qi} data-testid={`question-${qi}`}>
          <span>{q.question}</span>
          {(q.options || []).map((opt, oi) => (
            <button
              key={oi}
              data-testid={`option-${qi}-${oi}`}
              onClick={() => selectAnswer(q.question, opt.label)}
              className={selectedAnswers[q.question] === opt.label && !showOtherInput[q.question] ? 'selected' : ''}
            >
              {opt.label}
            </button>
          ))}
          <button data-testid={`other-${qi}`} onClick={() => toggleOther(q.question)}>
            Other...
          </button>
          {showOtherInput[q.question] && (
            <input
              data-testid={`other-input-${qi}`}
              value={otherInputs[q.question] || ''}
              onChange={e => setOtherAnswer(q.question, e.target.value)}
              placeholder="Type your answer..."
            />
          )}
        </div>
      ))}
      {freeTextMode && (
        <textarea
          data-testid="free-text-input"
          value={freeTextInput}
          onChange={e => setFreeTextInput(e.target.value)}
          placeholder="Type your response..."
        />
      )}
      <button
        data-testid="submit-all"
        disabled={submitDisabled}
        onClick={() => freeTextMode ? onSubmit({ _free_text: freeTextInput.trim() }) : onSubmit(selectedAnswers)}
      >
        Submit Answers
      </button>
    </div>
  )
}

function ChatInput({
  taskStatus,
  onSend,
}: {
  taskStatus: string
  onSend: (content: string) => void
}) {
  const [input, setInput] = useState('')
  const isRunning = ['designing', 'running'].includes(taskStatus)

  if (!isRunning) return null

  return (
    <div data-testid="chat-input-area">
      <input
        data-testid="chat-input"
        value={input}
        onChange={e => setInput(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter') {
            onSend(input.trim())
            setInput('')
          }
        }}
        placeholder="Type a message..."
      />
      <button
        data-testid="chat-send"
        disabled={!input.trim()}
        onClick={() => { onSend(input.trim()); setInput('') }}
      >
        Send
      </button>
    </div>
  )
}

describe('QuestionBanner', () => {
  const twoQuestions: Question[] = [
    { question: 'Which platform?', options: [{ label: 'Amazon' }, { label: 'Noon' }] },
    { question: 'Which country?', options: [{ label: 'US' }, { label: 'AE' }] },
  ]

  it('does NOT submit when only one option is clicked', () => {
    const onSubmit = vi.fn()
    render(<QuestionBanner questions={twoQuestions} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByTestId('option-0-0'))
    expect(screen.getByTestId('submit-all')).toBeDisabled()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('submits all answers when all questions answered', () => {
    const onSubmit = vi.fn()
    render(<QuestionBanner questions={twoQuestions} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByTestId('option-0-0')) // Amazon
    fireEvent.click(screen.getByTestId('option-1-1')) // AE

    expect(screen.getByTestId('submit-all')).not.toBeDisabled()
    fireEvent.click(screen.getByTestId('submit-all'))
    expect(onSubmit).toHaveBeenCalledWith({
      'Which platform?': 'Amazon',
      'Which country?': 'AE',
    })
  })

  it('highlights selected answer', () => {
    const onSubmit = vi.fn()
    render(<QuestionBanner questions={twoQuestions} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByTestId('option-0-1')) // Noon
    expect(screen.getByTestId('option-0-1').className).toContain('selected')
    expect(screen.getByTestId('option-0-0').className).not.toContain('selected')
  })

  it('clicking Other deselects predefined option (mutual exclusion)', () => {
    const onSubmit = vi.fn()
    render(<QuestionBanner questions={twoQuestions} onSubmit={onSubmit} />)

    // Select a predefined option
    fireEvent.click(screen.getByTestId('option-0-0')) // Amazon
    expect(screen.getByTestId('option-0-0').className).toContain('selected')

    // Click Other — predefined should be deselected
    fireEvent.click(screen.getByTestId('other-0'))
    expect(screen.getByTestId('other-input-0')).toBeInTheDocument()
    expect(screen.getByTestId('option-0-0').className).not.toContain('selected')
  })

  it('clicking predefined option closes Other input', () => {
    const onSubmit = vi.fn()
    render(<QuestionBanner questions={twoQuestions} onSubmit={onSubmit} />)

    // Open Other
    fireEvent.click(screen.getByTestId('other-0'))
    expect(screen.getByTestId('other-input-0')).toBeInTheDocument()

    // Click predefined — Other should close
    fireEvent.click(screen.getByTestId('option-0-0'))
    expect(screen.queryByTestId('other-input-0')).not.toBeInTheDocument()
    expect(screen.getByTestId('option-0-0').className).toContain('selected')
  })

  it('shows free-text textarea when dismiss toggle is clicked', () => {
    const onSubmit = vi.fn()
    render(<QuestionBanner questions={twoQuestions} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByTestId('toggle-free-text'))
    expect(screen.getByTestId('free-text-input')).toBeInTheDocument()
    expect(screen.queryByTestId('question-0')).not.toBeInTheDocument()
    expect(screen.queryByTestId('question-1')).not.toBeInTheDocument()
  })

  it('submits free-text answer with _free_text key', () => {
    const onSubmit = vi.fn()
    render(<QuestionBanner questions={twoQuestions} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByTestId('toggle-free-text'))
    fireEvent.change(screen.getByTestId('free-text-input'), { target: { value: 'my custom answer' } })
    fireEvent.click(screen.getByTestId('submit-all'))
    expect(onSubmit).toHaveBeenCalledWith({ _free_text: 'my custom answer' })
  })

  it('submit disabled when free-text is empty', () => {
    const onSubmit = vi.fn()
    render(<QuestionBanner questions={twoQuestions} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByTestId('toggle-free-text'))
    expect(screen.getByTestId('submit-all')).toBeDisabled()
  })

  it('can switch back to structured mode', () => {
    const onSubmit = vi.fn()
    render(<QuestionBanner questions={twoQuestions} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByTestId('toggle-free-text'))
    expect(screen.queryByTestId('question-0')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('toggle-free-text'))
    expect(screen.getByTestId('question-0')).toBeInTheDocument()
    expect(screen.getByTestId('question-1')).toBeInTheDocument()
  })

  it('preserves structured answers when switching to free-text and back', () => {
    const onSubmit = vi.fn()
    render(<QuestionBanner questions={twoQuestions} onSubmit={onSubmit} />)

    // Select an option for q0
    fireEvent.click(screen.getByTestId('option-0-0')) // Amazon
    expect(screen.getByTestId('option-0-0').className).toContain('selected')

    // Toggle to free-text and back
    fireEvent.click(screen.getByTestId('toggle-free-text'))
    fireEvent.click(screen.getByTestId('toggle-free-text'))

    // q0 should still be selected
    expect(screen.getByTestId('option-0-0').className).toContain('selected')
  })

  it('Other text counts as answer', () => {
    const onSubmit = vi.fn()
    render(<QuestionBanner questions={twoQuestions} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByTestId('other-0'))
    fireEvent.change(screen.getByTestId('other-input-0'), { target: { value: 'eBay' } })
    fireEvent.click(screen.getByTestId('option-1-0')) // US

    expect(screen.getByTestId('submit-all')).not.toBeDisabled()
    fireEvent.click(screen.getByTestId('submit-all'))
    expect(onSubmit).toHaveBeenCalledWith({
      'Which platform?': 'eBay',
      'Which country?': 'US',
    })
  })
})

describe('ChatInput', () => {
  it('shows input when task is running', () => {
    render(<ChatInput taskStatus="running" onSend={vi.fn()} />)
    expect(screen.getByTestId('chat-input-area')).toBeInTheDocument()
  })

  it('shows input when task is designing', () => {
    render(<ChatInput taskStatus="designing" onSend={vi.fn()} />)
    expect(screen.getByTestId('chat-input-area')).toBeInTheDocument()
  })

  it('hides input when task is completed', () => {
    render(<ChatInput taskStatus="completed" onSend={vi.fn()} />)
    expect(screen.queryByTestId('chat-input-area')).not.toBeInTheDocument()
  })

  it('calls onSend with content on Enter', () => {
    const onSend = vi.fn()
    render(<ChatInput taskStatus="running" onSend={onSend} />)

    const input = screen.getByTestId('chat-input')
    fireEvent.change(input, { target: { value: 'I logged in' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onSend).toHaveBeenCalledWith('I logged in')
  })

  it('calls onSend on button click', () => {
    const onSend = vi.fn()
    render(<ChatInput taskStatus="running" onSend={onSend} />)

    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'done' } })
    fireEvent.click(screen.getByTestId('chat-send'))

    expect(onSend).toHaveBeenCalledWith('done')
  })

  it('disables send button when input is empty', () => {
    render(<ChatInput taskStatus="running" onSend={vi.fn()} />)
    expect(screen.getByTestId('chat-send')).toBeDisabled()
  })
})
