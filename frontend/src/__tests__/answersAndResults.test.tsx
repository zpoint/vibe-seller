/**
 * Answers read as question, then answer, whatever path took them; a turn
 * shows one result card live, as on reload.
 */
import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import { makeConversationItem, renderConversationStream } from '../test/helpers'
import { parseAnswerBubble } from '../lib/answerBubble'
import { addLiveResult, buildConversationItems, refreshLatestResult } from '../lib/conversation'
import type { ConversationItem, Task } from '../types'

const pairs = (n: number) =>
  Array.from({ length: n }, (_, i) => `Q: question ${i + 1}?\nA: answer ${i + 1}`).join('\n\n')

describe('answer shapes', () => {
  it.each([1, 2, 3, 4])('%i questions parse to as many pairs', n => {
    const got = parseAnswerBubble(pairs(n))!
    expect(got).toHaveLength(n)
    expect(got[n - 1]).toEqual({ question: `question ${n}?`, answer: `answer ${n}` })
  })

  it('keeps a multi-line custom answer whole', () => {
    expect(parseAnswerBubble('Q: Tone?\nA: warm\nno slang')).toEqual([
      { question: 'Tone?', answer: 'warm\nno slang' },
    ])
  })

  it('shows one free-text reply once under all the questions', () => {
    expect(parseAnswerBubble('Q: Country?\nTone?\nA: Saudi, formal')).toEqual([
      { question: 'Country?\nTone?', answer: 'Saudi, formal' },
    ])
  })
})

describe('a live answer', () => {
  it('rebuilds on reload as the same question-and-answer card', () => {
    const items = buildConversationItems(
      [{ role: 'answer', content: pairs(2), created_at: '2026-11-25T10:00:00Z' }],
      { id: 't1', status: 'completed', plan_mode: false } as Task,
    )
    expect(items.map(i => i.type)).toEqual(['answer'])
    renderConversationStream({ items, isActive: false })
    expect(screen.getAllByText('Question')).toHaveLength(2)
    expect(screen.getByText('answer 2')).toBeInTheDocument()
  })
})

describe('one result per turn, live', () => {
  const res = (result: string) =>
    makeConversationItem('result', { result }) as ConversationItem & { type: 'result' }
  const results = (items: ConversationItem[]) =>
    items.filter(i => i.type === 'result').map(i => i.result)

  it('the turn result takes the recorded card, keeping the recorded text', () => {
    let items: ConversationItem[] = []
    items = addLiveResult(items, res('recorded'), true)
    items = [...items, makeConversationItem('agent_message', { message: { role: 'assistant', content: 'turn text' } })]
    items = addLiveResult(items, res('turn text'), false)
    expect(results(items)).toEqual(['recorded'])
    expect(items.some(i => i.type === 'agent_message')).toBe(false)
    // and a reload draws the same: one card, task.result
    expect(results(refreshLatestResult(items, 'recorded'))).toEqual(['recorded'])
  })

  it('an empty turn result leaves the recorded card alone', () => {
    let items = addLiveResult([], res('recorded'), true)
    items = addLiveResult(items, res(''), false)
    expect(results(items)).toEqual(['recorded'])
  })

  it('later turns get their own card', () => {
    let items = addLiveResult([], res('first'), true)
    items = addLiveResult(items, res('first'), false)
    items = addLiveResult(items, res('second'), false)
    expect(results(items)).toEqual(['first', 'second'])
  })
})
