/**
 * The OPEN schedule follows schedule-list reloads.
 *
 * Plan events (a plan proposed, approved, re-planned) reload the list.
 * The detail view and its plan panel must show the reloaded row, not
 * the snapshot taken when the schedule was selected — otherwise a
 * re-planned schedule keeps showing "Planning" with no Approve button
 * while the list (and the top banner) already say it waits for you.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { makeSchedule, makeStore } from '../test/factories'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const STORES = [makeStore({ id: 'store-a', name: 'Demo A' })]

describe('open schedule follows reloads', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('a plan event moves the open plan panel from planning to approval', async () => {
    let status: 'planning' | 'pending_approval' = 'planning'
    const row = () => makeSchedule({
      id: 'sch-1', store_id: 'store-a', title: 'Weekly check',
      plan_mode: true, plan_status: status, plan_version: status === 'planning' ? 1 : 2,
    })
    window.history.pushState({}, '', '/stores/store-a/schedules/sch-1')
    const { emit } = await bootApp({
      route: (url: string) => {
        if (url === '/api/stores') return STORES
        if (url === '/api/schedules') return [row()]
        if (url === '/api/schedules/attention') return { plan_approvals: [] }
        if (url === '/api/settings') return {}
        if (url.endsWith('/plan')) {
          return {
            plan_status: status, plan_version: row().plan_version,
            plan_text: null,
            proposed_plan: status === 'planning' ? null : '## Revised',
            plan_error: null, current_planning_task_id: null,
            planning_task_history: [],
          }
        }
        return undefined
      },
    })
    expect(await screen.findByTestId('schedule-plan-panel')).toBeInTheDocument()
    expect(screen.queryByTestId('plan-approve')).toBeNull()

    status = 'pending_approval'
    emit({ type: 'schedule_plan_pending_approval', schedule_id: 'sch-1', plan_version: 2 })
    for (let i = 0; i < 5; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)) })
    expect(await screen.findByTestId('plan-approve')).toBeInTheDocument()
  })
})
