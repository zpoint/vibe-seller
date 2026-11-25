/**
 * The user's answers to a parked question read as question, then answer.
 *
 * Observed: `question → answer` on one line ran a long question and its
 * answer together, and two answers read as one paragraph.
 */
import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import { makeConversationItem, renderConversationStream } from '../test/helpers'
import { parseAnswerBubble } from '../lib/answerBubble'

const BUBBLE = 'Q: Which route?\nThe bot is not in the group yet.\nA: Add the bot first\n\nQ: Reply to the wrong total?\nA: Not yet'

describe('answer bubble', () => {
  it('parses each question with its answer, multi-line questions included', () => {
    expect(parseAnswerBubble(BUBBLE)).toEqual([
      { question: 'Which route?\nThe bot is not in the group yet.', answer: 'Add the bot first' },
      { question: 'Reply to the wrong total?', answer: 'Not yet' },
    ])
  })

  it('leaves an ordinary message alone', () => {
    expect(parseAnswerBubble('move A → B please')).toBeNull()
    expect(parseAnswerBubble('Q: a question with no answer')).toBeNull()
  })

  it('renders question and answer as separate blocks', () => {
    renderConversationStream({
      items: [makeConversationItem('user_message', { message: { role: 'user', content: BUBBLE } })],
      isActive: false,
    })
    expect(screen.getAllByText('Question')).toHaveLength(2)
    expect(screen.getAllByText('Answer')).toHaveLength(2)
    expect(screen.getByText('Add the bot first')).toBeInTheDocument()
    expect(screen.getByText('Not yet')).toBeInTheDocument()
  })
})
