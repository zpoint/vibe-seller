/**
 * PlanCard approve-button visibility — regression guard for the
 * plan-at-creation bug where a plan-only Task owned by a Schedule
 * had its approve button hidden because the `!scheduleId` condition
 * didn't know the task was a creation-time planner.
 *
 * Without the fix, `test_plan_only_task_shows_confirm` fails with
 * the button missing from the DOM.
 */
import { render, screen } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { describe, expect, it, vi } from 'vitest'

import { PlanCard } from '../components/conversation/PlanCard'
import { i18nTestInstance } from '../test/helpers'
import type { PlanVersion } from '../types'

function plan(overrides: Partial<PlanVersion> = {}): PlanVersion {
  return {
    version: 1,
    content: '## Plan\n\n1. Do the thing',
    isCurrent: true,
    ...overrides,
  }
}

function renderCard(props: {
  taskStatus?: string
  planMode?: boolean
  scheduleId?: string | null
  onConfirm?: (() => void) | undefined
}) {
  const onConfirm = props.onConfirm === undefined ? vi.fn() : props.onConfirm
  render(
    <I18nextProvider i18n={i18nTestInstance}>
      <PlanCard
        plan={plan()}
        todoItems={[]}
        taskStatus={props.taskStatus ?? 'planned'}
        planMode={props.planMode ?? true}
        scheduleId={props.scheduleId ?? null}
        onConfirm={onConfirm ?? undefined}
      />
    </I18nextProvider>,
  )
}

describe('PlanCard approve button', () => {
  it('shows confirm for an ad-hoc plan-mode task (no schedule)', () => {
    renderCard({ scheduleId: null })
    // The confirm text comes from i18n `tasks.confirmAndExecute`.
    // We don't check the exact wording — just that something
    // clickable appears in the confirm slot.
    const confirmByText = screen.queryAllByRole('button').map(b => b.textContent || '')
    expect(
      confirmByText.some(t => t.includes('Confirm') || t.includes('执行')),
    ).toBe(true)
  })

  it('HIDES confirm for a scheduled fire (schedule_id set, not plan-only)', () => {
    // Scheduled fires auto-approve server-side; button must not show.
    renderCard({ scheduleId: 'sched-1' })
    const buttons = screen.queryAllByRole('button').map(b => b.textContent || '')
    expect(
      buttons.some(t => t.includes('Confirm') || t.includes('Execute')),
    ).toBe(false)
  })

  it('HIDES confirm for a plan-only task owned by a schedule', () => {
    // Plan-only tasks are now auto-approved server-side (the gate
    // in app/task_runner_auto.py auto-approves any schedule_id
    // task). Surfacing a confirm button would be misleading — the
    // task is already COMPLETED by the time the UI paints, and a
    // click would POST /execute-plan on a COMPLETED task and 400.
    renderCard({ scheduleId: 'sched-1' })
    const buttons = screen.queryAllByRole('button').map(b => b.textContent || '')
    expect(
      buttons.some(t => t.includes('Confirm') || t.includes('Approve')),
    ).toBe(false)
  })

  it('hides confirm when task is not in planned status', () => {
    renderCard({ taskStatus: 'running', scheduleId: 'sched-1' })
    const buttons = screen.queryAllByRole('button').map(b => b.textContent || '')
    expect(
      buttons.some(t => t.includes('Confirm') || t.includes('Execute')),
    ).toBe(false)
  })

  it('hides confirm when plan_mode is false', () => {
    renderCard({ planMode: false, scheduleId: 'sched-1' })
    const buttons = screen.queryAllByRole('button').map(b => b.textContent || '')
    expect(
      buttons.some(t => t.includes('Confirm') || t.includes('Execute')),
    ).toBe(false)
  })
})
