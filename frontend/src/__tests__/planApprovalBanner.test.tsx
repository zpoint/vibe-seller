/**
 * The top "plans waiting for your approval" banner.
 *
 * A review schedule runs nothing until a person approves its plan, so
 * the wait is shown above every view (settings included), refreshed
 * when a plan event arrives, and one click opens the oldest waiting
 * schedule in its own store scope.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { makeSchedule, makeStore } from '../test/factories'
import type { PlanApprovalItem } from '../types'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const STORES = [makeStore({ id: 'store-a', name: 'Demo A' })]
const OLDER = makeSchedule({
  id: 'sch-old', store_id: 'store-a', title: 'Weekly check',
  plan_mode: true, plan_status: 'pending_approval', plan_version: 1,
})
const NEWER = makeSchedule({
  id: 'sch-new', store_id: null, title: 'Monthly report',
  plan_mode: true, plan_status: 'pending_approval', plan_version: 1,
})
const item = (s: typeof OLDER, at: string): PlanApprovalItem => ({
  schedule_id: s.id, title: s.title, store_id: s.store_id,
  plan_version: 1, proposed_at: at,
})

function routeWith(approvals: () => PlanApprovalItem[]) {
  return (url: string) => {
    if (url === '/api/stores') return STORES
    if (url === '/api/schedules') return [NEWER, OLDER]
    if (url === '/api/schedules/attention') return { plan_approvals: approvals() }
    if (url === '/api/settings') return {}
    if (url.endsWith('/plan')) {
      return {
        plan_status: 'pending_approval', plan_version: 1, plan_text: null,
        proposed_plan: '## Steps', plan_error: null,
        current_planning_task_id: null, planning_task_history: [],
      }
    }
    return undefined
  }
}

describe('plan approval banner', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('shows the count on any view and opens the oldest waiting schedule', async () => {
    window.history.pushState({}, '', '/settings')
    // Newest first from the server: the banner must still pick the oldest.
    const { router } = await bootApp({
      route: routeWith(() => [
        item(NEWER, '2026-11-02T00:00:00Z'),
        item(OLDER, '2026-11-01T00:00:00Z'),
      ]),
    })
    const banner = await screen.findByTestId('plan-approval-banner')
    expect(banner.textContent).toContain(
      '2 scheduled tasks have a plan waiting for your approval',
    )
    await act(async () => { fireEvent.click(banner) })
    for (let i = 0; i < 5; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)) })
    expect(router.state.location.pathname).toBe('/stores/store-a/schedules/sch-old')
    expect(await screen.findByTestId('plan-approve')).toBeInTheDocument()
  })

  it('is hidden when nothing waits and appears on a plan event', async () => {
    window.history.pushState({}, '', '/tasks')
    let approvals: PlanApprovalItem[] = []
    const { emit } = await bootApp({ route: routeWith(() => approvals) })
    expect(screen.queryByTestId('plan-approval-banner')).toBeNull()
    approvals = [item(OLDER, '2026-11-01T00:00:00Z')]
    emit({ type: 'schedule_plan_pending_approval', schedule_id: 'sch-old', plan_version: 1 })
    const banner = await screen.findByTestId('plan-approval-banner')
    expect(banner.textContent).toContain('1 scheduled task has a plan waiting')
  })
})

describe('planner question banner', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('appears when a planner asks and opens its planning task', async () => {
    window.history.pushState({}, '', '/tasks')
    let questions: unknown[] = []
    const { emit, router } = await bootApp({
      route: (url: string) => {
        if (url === '/api/schedules/attention') {
          return { plan_approvals: [], planner_questions: questions }
        }
        return routeWith(() => [])(url)
      },
    })
    expect(screen.queryByTestId('planner-question-banner')).toBeNull()
    questions = [{
      schedule_id: 'sch-old', title: 'Weekly check', store_id: 'store-a',
      task_id: 'plan-task-1',
    }]
    // A question on any task re-checks what waits for the user.
    emit({ type: 'task_questions', task_id: 'plan-task-1', request_id: 'r1', questions: [] })
    const banner = await screen.findByTestId('planner-question-banner')
    expect(banner.textContent).toContain("The AI has a question about 1 scheduled task's plan")
    await act(async () => { fireEvent.click(banner) })
    for (let i = 0; i < 5; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)) })
    expect(router.state.location.pathname).toBe('/tasks/plan-task-1')
  })
})
