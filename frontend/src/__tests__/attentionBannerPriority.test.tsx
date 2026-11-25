/**
 * The top bar: ONE at a time, answers before approvals.
 *
 * A task waiting for a reply stops for good at its deadline, so the
 * wait is shown above every view. When there is also a schedule plan to
 * approve, or a planner with a question, the user sees one bar, never a
 * stack: tasks waiting for a reply, then planner questions, then plans
 * to approve. Handling the last of one kind brings up the next.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { makeSchedule, makeStore } from '../test/factories'
import type { PlanApprovalItem, PlannerQuestionItem, WaitingTaskItem } from '../types'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const STORES = [makeStore({ id: 'store-a', name: 'Demo A' })]
const SCHEDULE = makeSchedule({
  id: 'sch-1', store_id: 'store-a', title: 'Weekly check',
  plan_mode: true, plan_status: 'pending_approval', plan_version: 1,
})
const waiting = (id: string, since: string): WaitingTaskItem => ({
  task_id: id, title: `Task ${id}`, store_id: null, kind: 'question',
  since, deadline: null,
})
const APPROVAL: PlanApprovalItem = {
  schedule_id: 'sch-1', title: 'Weekly check', store_id: 'store-a',
  plan_version: 1, proposed_at: '2026-11-01T00:00:00Z',
}
const PLANNER_Q: PlannerQuestionItem = {
  schedule_id: 'sch-1', title: 'Weekly check', store_id: 'store-a',
  task_id: 'plan-task-1',
}

interface Waits {
  tasks: WaitingTaskItem[]
  questions: PlannerQuestionItem[]
  approvals: PlanApprovalItem[]
}

function routeWith(waits: () => Waits) {
  return (url: string) => {
    if (url === '/api/stores') return STORES
    if (url === '/api/schedules') return [SCHEDULE]
    if (url === '/api/schedules/attention') {
      return { plan_approvals: waits().approvals, planner_questions: waits().questions }
    }
    if (url === '/api/tasks/attention') return { waiting_tasks: waits().tasks }
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

const settle = async () => {
  for (let i = 0; i < 5; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)) })
}
const bars = () => ['waiting-task-banner', 'planner-question-banner', 'task-plan-banner', 'plan-approval-banner']
  .filter(id => screen.queryByTestId(id))

describe('top attention bar', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('shows the count on any view and opens the task that waited longest', async () => {
    window.history.pushState({}, '', '/settings')
    // The server sends the oldest wait first.
    const { router } = await bootApp({
      route: routeWith(() => ({
        tasks: [waiting('t-old', '2026-11-01T00:00:00Z'), waiting('t-new', '2026-11-02T00:00:00Z')],
        questions: [], approvals: [],
      })),
    })
    const banner = await screen.findByTestId('waiting-task-banner')
    expect(banner.textContent).toContain('2 tasks are waiting for your reply')
    await act(async () => { fireEvent.click(banner) })
    await settle()
    expect(router.state.location.pathname).toBe('/tasks/t-old')
  })

  it('is hidden when nothing waits, appears when a task parks and goes when it is answered', async () => {
    window.history.pushState({}, '', '/tasks')
    const waits: Waits = { tasks: [], questions: [], approvals: [] }
    const { emit } = await bootApp({ route: routeWith(() => waits) })
    await settle()
    expect(bars()).toEqual([])

    waits.tasks = [waiting('t-1', '2026-11-01T00:00:00Z')]
    emit({ type: 'task_update', task_id: 't-1', status: 'waiting' })
    const banner = await screen.findByTestId('waiting-task-banner')
    expect(banner.textContent).toContain('1 task is waiting for your reply')

    waits.tasks = []
    emit({ type: 'task_update', task_id: 't-1', status: 'queued' })
    await settle()
    expect(bars()).toEqual([])
  })

  it('a reply to give comes before a plan to approve, which follows once answered', async () => {
    window.history.pushState({}, '', '/tasks')
    const waits: Waits = {
      tasks: [waiting('t-1', '2026-11-01T00:00:00Z')], questions: [], approvals: [APPROVAL],
    }
    const { emit, router } = await bootApp({ route: routeWith(() => waits) })
    await screen.findByTestId('waiting-task-banner')
    await settle()
    expect(bars()).toEqual(['waiting-task-banner'])

    waits.tasks = []
    emit({ type: 'task_update', task_id: 't-1', status: 'queued' })
    const approval = await screen.findByTestId('plan-approval-banner')
    expect(bars()).toEqual(['plan-approval-banner'])
    await act(async () => { fireEvent.click(approval) })
    await settle()
    expect(router.state.location.pathname).toBe('/stores/store-a/schedules/sch-1')
  })

  it('a planner question also comes before a plan to approve', async () => {
    window.history.pushState({}, '', '/tasks')
    const waits: Waits = { tasks: [], questions: [PLANNER_Q], approvals: [APPROVAL] }
    const { emit } = await bootApp({ route: routeWith(() => waits) })
    await screen.findByTestId('planner-question-banner')
    await settle()
    expect(bars()).toEqual(['planner-question-banner'])

    waits.questions = []
    emit({ type: 'task_update', task_id: 'plan-task-1', status: 'queued' })
    await screen.findByTestId('plan-approval-banner')
    expect(bars()).toEqual(['plan-approval-banner'])
  })

  it('walks every kind one click at a time until nothing waits', async () => {
    window.history.pushState({}, '', '/settings')
    const item = (id: string, kind: WaitingTaskItem['kind'], since: string, store_id: string | null = null): WaitingTaskItem => ({
      task_id: id, title: `Task ${id}`, store_id, kind, since, deadline: null,
    })
    // A no-store task's question, a store task's question, a sub-task's
    // manual wait, a plan-mode task's plan — plus a planner's question
    // and a schedule plan to approve.
    const waits: Waits = {
      tasks: [
        item('t-nostore', 'question', '2026-11-01T00:00:00Z'),
        item('t-store', 'question', '2026-11-02T00:00:00Z', 'store-a'),
        item('t-plan', 'plan', '2026-11-02T12:00:00Z'),
        item('t-subtask', 'reply', '2026-11-03T00:00:00Z', 'store-a'),
      ],
      questions: [PLANNER_Q],
      approvals: [APPROVAL],
    }
    const { emit, router } = await bootApp({ route: routeWith(() => waits) })
    const path = () => router.state.location.pathname
    const resolve = async (id: string) => {
      waits.tasks = waits.tasks.filter(t => t.task_id !== id)
      emit({ type: 'task_update', task_id: id, status: 'queued' })
      await settle()
    }
    const click = async (testId: string) => {
      const bar = await screen.findByTestId(testId)
      expect(bars()).toEqual([testId])
      await act(async () => { fireEvent.click(bar) })
      await settle()
      return bar
    }

    // Replies first, oldest first, whatever the kind of task.
    expect((await screen.findByTestId('waiting-task-banner')).textContent).toContain('3 tasks are waiting for your reply')
    await click('waiting-task-banner')
    expect(path()).toBe('/tasks/t-nostore')
    await resolve('t-nostore')
    expect(screen.getByTestId('waiting-task-banner').textContent).toContain('2 tasks are waiting for your reply')
    await click('waiting-task-banner')
    // A store task opens inside its store, so the list beside it matches.
    expect(path()).toBe('/stores/store-a/tasks/t-store')
    await resolve('t-store')
    expect(screen.getByTestId('waiting-task-banner').textContent).toContain('1 task is waiting for your reply')
    await click('waiting-task-banner')
    expect(path()).toBe('/stores/store-a/tasks/t-subtask')
    await resolve('t-subtask')

    // Then the planner's question.
    await click('planner-question-banner')
    expect(path()).toBe('/stores/store-a/tasks/plan-task-1')
    waits.questions = []
    emit({ type: 'task_update', task_id: 'plan-task-1', status: 'queued' })
    await settle()

    // Then plans to review: the task's, then the schedule's.
    expect((await screen.findByTestId('task-plan-banner')).textContent).toContain('1 task has a plan waiting for your review')
    await click('task-plan-banner')
    expect(path()).toBe('/tasks/t-plan')
    await resolve('t-plan')
    await click('plan-approval-banner')
    expect(path()).toBe('/stores/store-a/schedules/sch-1')
    waits.approvals = []
    emit({ type: 'schedule_plan_ready', schedule_id: 'sch-1', plan_version: 1 })
    await settle()
    expect(bars()).toEqual([])
  })
})
