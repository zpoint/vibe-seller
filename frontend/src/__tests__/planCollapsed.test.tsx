/**
 * A plan is shown in full only while the user has to read it to decide.
 *
 * Observed: after the user answered a question in a running task, its
 * revised plan rendered again in full at the bottom of the conversation —
 * a page of text nobody had to act on, placed as if it were new.
 */
import { describe, it, expect, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'
import {
  makeConversationItem,
  makePlan,
  renderConversationStream,
} from '../test/helpers'
import { buildConversationItems } from '../lib/conversation'
import type { ConversationItem, Task } from '../types'

const revised = () => [
  makeConversationItem('plan', { plan: makePlan({ version: 1, content: '## A\n1. Old step', isCurrent: false }) }),
  makeConversationItem('plan', { plan: makePlan({ version: 2, content: '## B\n1. New step', isCurrent: true }) }),
]

describe('plan card default', () => {
  it('is open while the plan waits for the user to confirm it', () => {
    renderConversationStream({
      items: revised(),
      task: { status: 'planned', plan_mode: true, schedule_id: null },
      onConfirmPlan: vi.fn(),
    })
    expect(screen.getByText('New step')).toBeInTheDocument()
  })

  it('is one line once nobody has to confirm it, and opens on click', () => {
    renderConversationStream({
      items: revised(),
      task: { status: 'running', plan_mode: false, schedule_id: null },
    })
    expect(screen.queryByText('New step')).not.toBeInTheDocument()
    expect(screen.getByText('Plan updated')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Plan updated'))
    expect(screen.getByText('New step')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Hide'))
    expect(screen.queryByText('New step')).not.toBeInTheDocument()
  })

  it('collapses a first plan the user already approved', () => {
    renderConversationStream({
      items: [makeConversationItem('plan', { plan: makePlan({ version: 1, content: '## A\n1. Only step', isCurrent: true }) })],
      task: { status: 'completed', plan_mode: true, schedule_id: null },
    })
    expect(screen.queryByText('Only step')).not.toBeInTheDocument()
  })
})

describe('plan position on reload', () => {
  const task = (over: Partial<Task>) =>
    ({ id: 't1', status: 'running', plan_mode: false, ...over }) as Task

  it('puts each plan where it was written, and execution after the current one', () => {
    const items = buildConversationItems(
      [
        { role: 'user', content: 'make a plan', created_at: '2026-11-24T08:00:00Z' },
        { role: 'user', content: 'add a reply step', created_at: '2026-11-24T08:11:00Z' },
        { role: 'assistant', content: 'running it', created_at: '2026-11-24T08:13:00Z' },
        { role: 'user', content: 'next day answer', created_at: '2026-11-25T03:00:00Z' },
      ],
      task({
        plan: 'v2',
        plan_history: JSON.stringify([
          { version: 1, content: 'v1', created_at: '2026-11-24T08:10:00Z' },
          { version: 2, content: 'v2', created_at: '2026-11-24T08:12:00Z' },
        ]),
      }),
    )
    const order = items.map((i: ConversationItem) =>
      i.type === 'plan' ? `plan${i.plan!.version}` : i.type === 'execution_separator' ? 'exec' : i.message!.content,
    )
    expect(order).toEqual([
      'make a plan', 'plan1', 'add a reply step', 'plan2', 'exec', 'running it', 'next day answer',
    ])
  })
})

describe('empty result', () => {
  it('draws no card for a turn that ended with nothing to say', () => {
    const { container } = renderConversationStream({
      items: [makeConversationItem('result', { result: '' })],
      isActive: false,
    })
    expect(container.textContent).not.toMatch(/result/i)
  })
})
