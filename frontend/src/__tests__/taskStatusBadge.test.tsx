/**
 * A task's status badge says what it waits for.
 *
 * PLANNED is two different waits. A plan-mode task's plan waiting for
 * the user's review reads exactly like a schedule plan waiting for
 * approval ("待审批"), and the plan card offers Confirm; a scheduled
 * fire sits in PLANNED only while it waits for its turn — its plan was
 * already approved on the schedule — so it reads "queued" and offers
 * nothing to approve. "已规划" asked the user to guess which.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'

import { StatusBadge } from '../components/ui/StatusBadge'
import { awaitsPlanReview, badgeStatus } from '../lib/taskBadge'
import { i18nTestInstance } from '../test/helpers'
import en from '../i18n/locales/en/translation.json'
import zh from '../i18n/locales/zh/translation.json'

function badgeText(task: { status: string; plan_mode?: boolean; schedule_id?: string | null }) {
  render(
    <I18nextProvider i18n={i18nTestInstance}>
      <StatusBadge task={task} />
    </I18nextProvider>,
  )
  return screen.getByTestId('task-status-badge').textContent
}

describe('task status badge', () => {
  it('a plan waiting for review reads like a schedule plan waiting for approval', () => {
    const task = { status: 'planned', plan_mode: true, schedule_id: null }
    expect(awaitsPlanReview(task)).toBe(true)
    expect(badgeText(task)).toBe(en.schedules.planStatus.pending_approval)
  })

  it('a scheduled fire queued in PLANNED reads queued, nothing to approve', () => {
    const task = { status: 'planned', plan_mode: true, schedule_id: 'sch-1' }
    expect(awaitsPlanReview(task)).toBe(false)
    expect(badgeText(task)).toBe(en.status.queued)
  })

  it('every other status is shown as itself', () => {
    for (const status of ['pending', 'queued', 'designing', 'running', 'waiting', 'completed', 'failed']) {
      expect(badgeStatus({ status, plan_mode: true, schedule_id: null })).toBe(status)
    }
  })

  it('the two "waiting for your approval" badges use the same words in every language', () => {
    for (const locale of [en, zh]) {
      expect(locale.status.pending_approval).toBe(locale.schedules.planStatus.pending_approval)
      expect('planned' in locale.status).toBe(false)
    }
    expect(zh.status.pending_approval).toBe('待审批')
  })
})
