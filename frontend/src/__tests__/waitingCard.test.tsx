/**
 * A WAITING task says what it waits for.
 *
 * A parent waiting on its subtasks (`{strategy: 'children'}`) continues
 * by itself when the last one finishes. It must not read as "Manual"
 * with an empty reason and a 30-day deadline — that tells the user they
 * have to act. An agent-declared wait keeps its reason and deadline.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { I18nextProvider } from 'react-i18next'

import { ConversationStream } from '../components/conversation/ConversationStream'
import { i18nTestInstance, makeTask } from '../test/helpers'
import type { Task } from '../types'

function renderWaiting(waitCondition: string, overrides: Partial<Task> = {}) {
  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <ConversationStream
        items={[]}
        todoItems={[]}
        task={makeTask({ status: 'waiting', wait_condition: waitCondition, ...overrides })}
        steps={[]}
        screenshots={{}}
        pendingQuestions={null}
        selectedAnswers={{}}
        otherInputs={{}}
        showOtherInput={{}}
        onSelectAnswer={() => {}}
        onSetAnswer={() => {}}
        onToggleOther={() => {}}
        onSetOtherAnswer={() => {}}
        onSubmitAll={() => {}}
        onConfirmPlan={() => {}}
        onRequestChanges={() => {}}
        onOpenVisionSetup={() => {}}
        questionBannerRef={createRef<HTMLDivElement>()}
        isActive={false}
      />
    </I18nextProvider>,
  )
}

describe('waiting card', () => {
  it('a parent waiting on subtasks says it continues by itself', () => {
    renderWaiting(JSON.stringify({ strategy: 'children' }))
    const t = i18nTestInstance.t
    expect(screen.getByText(t('waiting.subtasks'))).toBeTruthy()
    expect(screen.getByText(t('waiting.subtasksHint'))).toBeTruthy()
    expect(screen.queryByText(t('waiting.manual'))).toBeNull()
    expect(screen.queryByText(`${t('waiting.reason')}:`)).toBeNull()
    expect(screen.queryByText(`${t('waiting.maxDays')}:`)).toBeNull()
  })

  it('an agent-declared manual wait keeps its reason and deadline', () => {
    renderWaiting(
      JSON.stringify({
        check_strategy: 'manual',
        reason: 'Waiting for the supplier to confirm',
      }),
    )
    const t = i18nTestInstance.t
    expect(screen.getByText(t('waiting.manual'))).toBeTruthy()
    expect(screen.getByText('Waiting for the supplier to confirm')).toBeTruthy()
    expect(screen.getByText(`${t('waiting.maxDays')}:`)).toBeTruthy()
    expect(screen.queryByText(t('waiting.subtasksHint'))).toBeNull()
  })

  it('counts the days down to the deadline the server stops the task at', () => {
    const DAY = 86_400_000
    const since = new Date(Date.now() - 7 * DAY + 60 * 60 * 1000)
    renderWaiting(
      JSON.stringify({
        check_strategy: 'manual',
        reason: 'The agent asked a question',
        waiting_since: since.toISOString(),
        max_wait_days: 7,
      }),
    )
    const t = i18nTestInstance.t
    const row = screen.getByTestId('waiting-deadline')
    // One hour before the deadline is still "1 day", never "0 days".
    expect(row.textContent).toContain(t('waiting.daysRemaining', { days: 1 }))
    const deadline = new Date(since.getTime() + 7 * DAY)
    expect(row.textContent).toContain(
      t('waiting.deadlineAt', { time: deadline.toLocaleString() }),
    )
  })

  it('reads 0 days only once the deadline has passed', () => {
    const DAY = 86_400_000
    renderWaiting(
      JSON.stringify({
        check_strategy: 'manual',
        reason: 'The agent asked a question',
        waiting_since: new Date(Date.now() - 7 * DAY - 60_000).toISOString(),
        max_wait_days: 7,
      }),
    )
    const t = i18nTestInstance.t
    expect(screen.getByTestId('waiting-deadline').textContent).toContain(
      t('waiting.daysRemaining', { days: 0 }),
    )
  })

  it('a task stopped at its deadline still shows the question nobody answered', () => {
    renderWaiting(
      JSON.stringify({
        check_strategy: 'manual',
        reason: 'The agent asked a question',
        max_wait_days: 7,
        pending_question: {
          request_id: 'r1',
          questions: [{ header: 'Shipping', question: 'Which shipping option?', options: [{ label: 'Standard' }, { label: 'Express' }] }],
        },
      }),
      { status: 'failed', error: 'Nobody replied within 7 days', error_category: 'wait_timeout' },
    )
    const t = i18nTestInstance.t
    const card = screen.getByTestId('unanswered-question')
    expect(card.textContent).toContain(t('tasks.unansweredQuestion'))
    expect(card.textContent).toContain('Which shipping option?')
    expect(card.textContent).toContain('Express')
    // Read-only: the reply goes in the message box, not a card button.
    expect(screen.queryByRole('button', { name: 'Express' })).toBeNull()
  })
})
