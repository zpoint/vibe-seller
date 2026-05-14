/**
 * F1-F4: ConversationStream rendering tests.
 */
import { describe, it, expect, vi } from 'vitest'
import { screen, fireEvent } from '@testing-library/react'
import {
  makeConversationItem,
  makePlan,
  renderConversationStream,
} from '../test/helpers'
import type { ConversationItem } from '../types'

describe('F1: Basic conversation elements', () => {
  it('renders a plan card with version badge and content', () => {
    const planItem = makeConversationItem('plan', {
      plan: makePlan({ version: 1, content: '## Plan\n1. Step one', isCurrent: true }),
    })

    renderConversationStream({
      items: [planItem],
      task: { status: 'planned', plan_mode: true, schedule_id: null },
    })

    // Plan version badge
    expect(screen.getByText(/v1/i)).toBeInTheDocument()
    // Plan content rendered
    expect(screen.getByText('Step one')).toBeInTheDocument()
  })

  it('renders user and agent message bubbles', () => {
    const items: ConversationItem[] = [
      makeConversationItem('user_message', {
        message: { role: 'user', content: 'Check spam folder' },
      }),
      makeConversationItem('agent_message', {
        message: { role: 'assistant', content: 'On it!' },
      }),
    ]

    renderConversationStream({ items })

    expect(screen.getByText('Check spam folder')).toBeInTheDocument()
    expect(screen.getByText('On it!')).toBeInTheDocument()
  })

  it('renders execution separator', () => {
    const items: ConversationItem[] = [
      makeConversationItem('execution_separator'),
    ]

    renderConversationStream({ items })

    expect(screen.getByText(/execution started/i)).toBeInTheDocument()
  })

  it('renders result card with markdown', () => {
    const items: ConversationItem[] = [
      makeConversationItem('result', {
        result: 'Task **completed** successfully',
      }),
    ]

    renderConversationStream({ items })

    expect(screen.getByText(/completed/)).toBeInTheDocument()
  })
})

describe('F2: Replan rendering', () => {
  it('renders superseded plan as thin marker, current plan with confirm and version tabs', () => {
    const onConfirm = vi.fn()
    const items: ConversationItem[] = [
      makeConversationItem('plan', {
        plan: makePlan({ version: 1, content: '## Old Plan\n1. Old step', isCurrent: false }),
      }),
      makeConversationItem('user_message', {
        message: { role: 'user', content: 'Change the approach' },
      }),
      makeConversationItem('plan', {
        plan: makePlan({ version: 2, content: '## New Plan\n1. New step', isCurrent: true }),
      }),
    ]

    renderConversationStream({
      items,
      task: { status: 'planned', plan_mode: true, schedule_id: null },
      onConfirmPlan: onConfirm,
    })

    // Superseded plan shows as thin marker
    expect(screen.getByText(/Plan v1 superseded/i)).toBeInTheDocument()
    // Current plan shows confirm button
    const confirmButtons = screen.getAllByRole('button', { name: /confirm|execute/i })
    expect(confirmButtons.length).toBeGreaterThanOrEqual(1)
    // Version tabs (v1, v2) appear in current plan card
    expect(screen.getByRole('button', { name: 'v1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'v2' })).toBeInTheDocument()
    // User message between plans
    expect(screen.getByText('Change the approach')).toBeInTheDocument()
  })

  it('renders three rapid replans with version tabs', () => {
    const items: ConversationItem[] = [
      makeConversationItem('plan', {
        plan: makePlan({ version: 1, content: '## Plan v1\n1. First', isCurrent: false }),
      }),
      makeConversationItem('plan', {
        plan: makePlan({ version: 2, content: '## Plan v2\n1. Second', isCurrent: false }),
      }),
      makeConversationItem('plan', {
        plan: makePlan({ version: 3, content: '## Plan v3\n1. Third', isCurrent: true }),
      }),
    ]

    renderConversationStream({
      items,
      task: { status: 'planned', plan_mode: true, schedule_id: null },
    })

    // Two superseded markers
    const superseded = screen.getAllByText(/superseded/i)
    expect(superseded).toHaveLength(2)
    // Three version tabs on the current plan card
    expect(screen.getByRole('button', { name: 'v1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'v2' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'v3' })).toBeInTheDocument()
  })

  it('clicking version tab shows historical plan content', () => {
    const items: ConversationItem[] = [
      makeConversationItem('plan', {
        plan: makePlan({
          version: 1,
          content: '## Old Plan\n1. Old step',
          isCurrent: false,
        }),
      }),
      makeConversationItem('plan', {
        plan: makePlan({
          version: 2,
          content: '## New Plan\n1. New step',
          isCurrent: true,
        }),
      }),
    ]

    renderConversationStream({
      items,
      task: { status: 'planned', plan_mode: true, schedule_id: null },
    })

    // Initially shows current plan (v2) content
    expect(screen.getByText('New step')).toBeInTheDocument()
    // Click v1 tab to view old plan
    fireEvent.click(screen.getByRole('button', { name: 'v1' }))
    // Old plan content now visible, new plan content hidden
    expect(screen.getByText('Old step')).toBeInTheDocument()
    expect(screen.queryByText('New step')).not.toBeInTheDocument()
    // Shows "Back to current" link
    expect(screen.getByText(/back to current/i)).toBeInTheDocument()
    // Confirm button is hidden when viewing historical
    expect(screen.queryByRole('button', { name: /confirm|execute/i })).not.toBeInTheDocument()
  })

  it('single plan version shows badge instead of tabs', () => {
    const items: ConversationItem[] = [
      makeConversationItem('plan', {
        plan: makePlan({ version: 1, content: '## Plan\n1. Step', isCurrent: true }),
      }),
    ]

    renderConversationStream({
      items,
      task: { status: 'planned', plan_mode: true, schedule_id: null },
    })

    // No version tab buttons
    expect(screen.queryByRole('button', { name: 'v1' })).not.toBeInTheDocument()
    // Version badge shown instead
    expect(screen.getByText('Plan v1')).toBeInTheDocument()
  })
})

describe('F3: Execution flow', () => {
  it('shows separator + agent messages + user interrupt inline', () => {
    const items: ConversationItem[] = [
      makeConversationItem('plan', {
        plan: makePlan({ version: 1, isCurrent: true }),
      }),
      makeConversationItem('execution_separator'),
      makeConversationItem('agent_message', {
        message: { role: 'assistant', content: 'Starting execution...' },
      }),
      makeConversationItem('user_message', {
        message: { role: 'user', content: 'Also check spam' },
      }),
      makeConversationItem('agent_message', {
        message: { role: 'assistant', content: 'Checking spam now' },
      }),
    ]

    renderConversationStream({ items })

    expect(screen.getByText(/execution started/i)).toBeInTheDocument()
    expect(screen.getByText('Starting execution...')).toBeInTheDocument()
    expect(screen.getByText('Also check spam')).toBeInTheDocument()
    expect(screen.getByText('Checking spam now')).toBeInTheDocument()
  })
})

describe('F4: Edge cases', () => {
  it('shows spinner when empty + isActive', () => {
    renderConversationStream({
      items: [],
      task: { status: 'designing' },
      isActive: true,
    })

    expect(screen.getByText(/planning/i)).toBeInTheDocument()
  })

  it('shows streaming cursor element', () => {
    const items: ConversationItem[] = [
      makeConversationItem('streaming', {
        message: { role: '_streaming', content: 'working...' },
      }),
    ]

    const { container } = renderConversationStream({ items })

    expect(screen.getByText('working...')).toBeInTheDocument()
    // Streaming indicator (pulsing cursor)
    const cursor = container.querySelector('.animate-pulse')
    expect(cursor).toBeInTheDocument()
  })

  it('shows waiting task info card when status=waiting with condition', () => {
    const waitCondition = JSON.stringify({
      check_strategy: 'email',
      reason: 'Waiting for reply',
      waiting_since: new Date().toISOString(),
      max_wait_days: 7,
      keywords: ['confirmation'],
    })

    renderConversationStream({
      items: [],
      task: { status: 'waiting', wait_condition: waitCondition },
      isActive: false,
    })

    expect(screen.getByText(/waiting for reply/i)).toBeInTheDocument()
    expect(screen.getByText('confirmation')).toBeInTheDocument()
  })

  it('renders question banner in stream', () => {
    const items: ConversationItem[] = [
      makeConversationItem('question', {
        questions: {
          request_id: 'q1',
          questions: [{ question: 'Pick one', options: [{ label: 'X' }] }],
        },
      }),
    ]

    renderConversationStream({
      items,
      pendingQuestions: {
        request_id: 'q1',
        questions: [{ question: 'Pick one', options: [{ label: 'X' }] }],
      },
      selectedAnswers: {},
      showOtherInput: {},
      otherInputs: {},
    })

    expect(screen.getByText('Pick one')).toBeInTheDocument()
    expect(screen.getByText('X')).toBeInTheDocument()
  })

  it('renders answered questions as collapsed summary', () => {
    const items: ConversationItem[] = [
      makeConversationItem('question', {
        questions: {
          request_id: 'q-old',
          questions: [{ header: 'Setup', question: 'Pick a color' }],
        },
      }),
    ]

    renderConversationStream({
      items,
      pendingQuestions: null,
    })

    // Should show collapsed summary, not the full interactive banner
    expect(screen.getByText(/Questions answered/)).toBeInTheDocument()
    expect(screen.queryByText('Pick a color')).not.toBeInTheDocument()
  })
})

describe('F5: Task start card', () => {
  it('renders matching first user_message as TaskStartCard', () => {
    const items: ConversationItem[] = [
      makeConversationItem('user_message', {
        message: {
          role: 'user',
          content:
            'Design an execution plan for this task: Sync Emails\n\nDetails: Find March emails and export',
        },
      }),
    ]

    renderConversationStream({ items })

    expect(screen.getByText('Task')).toBeInTheDocument()
    expect(screen.getByText('Sync Emails')).toBeInTheDocument()
    // Description is collapsed by default
    expect(screen.queryByText('Find March emails and export')).not.toBeInTheDocument()
    // Click title to expand
    fireEvent.click(screen.getByText('Sync Emails'))
    expect(screen.getByText('Find March emails and export')).toBeInTheDocument()
    expect(
      screen.queryByText(/Design an execution plan for this task:/),
    ).not.toBeInTheDocument()
  })

  it('renders card without divider when no description', () => {
    const items: ConversationItem[] = [
      makeConversationItem('user_message', {
        message: {
          role: 'user',
          content: 'Design an execution plan for this task: Quick Task',
        },
      }),
    ]

    const { container } = renderConversationStream({ items })

    expect(screen.getByText('Task')).toBeInTheDocument()
    expect(screen.getByText('Quick Task')).toBeInTheDocument()
    const card = container.querySelector('[data-testid="task-start-card"]')
    expect(card).toBeInTheDocument()
    expect(card!.querySelector('.border-t')).not.toBeInTheDocument()
  })

  it('renders non-matching user_message as normal bubble', () => {
    const items: ConversationItem[] = [
      makeConversationItem('user_message', {
        message: { role: 'user', content: 'Check spam folder' },
      }),
    ]

    renderConversationStream({ items })

    expect(screen.getByText('Check spam folder')).toBeInTheDocument()
    expect(screen.queryByText('Task')).not.toBeInTheDocument()
  })

  it('only renders first matching user_message as card', () => {
    const items: ConversationItem[] = [
      makeConversationItem('user_message', {
        message: {
          role: 'user',
          content:
            'Design an execution plan for this task: First Task\n\nDetails: Desc one',
        },
      }),
      makeConversationItem('user_message', {
        message: {
          role: 'user',
          content:
            'Design an execution plan for this task: Second Task\n\nDetails: Desc two',
        },
      }),
    ]

    renderConversationStream({ items })

    expect(screen.getByText('First Task')).toBeInTheDocument()
    // Description collapsed by default; expand it
    fireEvent.click(screen.getByText('First Task'))
    expect(screen.getByText('Desc one')).toBeInTheDocument()
    expect(
      screen.getByText(
        /Design an execution plan for this task: Second Task/,
      ),
    ).toBeInTheDocument()
  })
})
