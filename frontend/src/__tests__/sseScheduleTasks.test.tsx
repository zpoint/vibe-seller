/**
 * Tests that SSE task_update events patch both `tasks` and
 * `scheduleTasks` state, and that `fanout_triggered` reloads
 * schedule tasks.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useSSE } from '../hooks/useSSE'
import type { Task } from '../types'

/* ── EventSource mock ─────────────────────────────── */

type Listener = ((evt: MessageEvent) => void) | null
let esInstances: { onmessage: Listener; close: ReturnType<typeof vi.fn> }[] = []

beforeEach(() => {
  esInstances = []
  const MockES = vi.fn().mockImplementation(() => {
    const inst = { onmessage: null as Listener, close: vi.fn() }
    esInstances.push(inst)
    return inst
  })
  vi.stubGlobal('EventSource', MockES)
})

afterEach(() => {
  vi.restoreAllMocks()
})

function emit(data: Record<string, unknown>) {
  const inst = esInstances[esInstances.length - 1]
  inst?.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
}

/* ── Minimal params factory ───────────────────────── */

function makeTask(id: string, status = 'pending'): Task {
  return {
    id, store_id: null, title: 'T', description: null,
    status, plan: null, result: null, todos: null,
    wait_condition: null, error: null, plan_mode: false,
    ai_profile_id: null, schedule_id: 's1', batch_id: null,
    created_at: '', started_at: null, completed_at: null,
  }
}

function makeParams(overrides: Record<string, unknown> = {}) {
  return {
    selectedTaskId: undefined as string | undefined,
    appView: 'tasks',
    setTasks: vi.fn(),
    setSelectedTask: vi.fn(),
    setSteps: vi.fn(),
    setScreenshots: vi.fn(),
    setAgentMessages: vi.fn(),
    setTodoItems: vi.fn(),
    setConversationItems: vi.fn(),
    setPendingQuestions: vi.fn(),
    setSelectedAnswers: vi.fn(),
    setOtherInputs: vi.fn(),
    setShowOtherInput: vi.fn(),
    setLogs: vi.fn(),
    questionBannerRef: { current: null },
    setScheduleTasks: vi.fn(),
    loadScheduleTasks: vi.fn(),
    loadSchedules: vi.fn(),
    loadTasks: vi.fn(),
    ...overrides,
  } as Parameters<typeof useSSE>[0]
}

/* ── Tests ────────────────────────────────────────── */

describe('SSE task_update patches scheduleTasks', () => {
  it('patches both tasks and scheduleTasks on task_update', () => {
    const params = makeParams()
    renderHook(() => useSSE(params))

    act(() => {
      emit({ type: 'task_update', task_id: 't1', status: 'running', error: null })
    })

    // setTasks called with a mapper function
    expect(params.setTasks).toHaveBeenCalled()
    // setScheduleTasks also called
    expect(params.setScheduleTasks).toHaveBeenCalled()

    // Verify the mapper produces the right status
    const task = makeTask('t1')
    const tasksMapper = params.setTasks.mock.calls[0][0] as (prev: Task[]) => Task[]
    expect(tasksMapper([task])[0].status).toBe('running')

    const schedMapper = (params.setScheduleTasks as ReturnType<typeof vi.fn>)
      .mock.calls[0][0] as (prev: Task[]) => Task[]
    expect(schedMapper([task])[0].status).toBe('running')
  })

  it('does not crash when setScheduleTasks is undefined', () => {
    const params = makeParams({ setScheduleTasks: undefined })
    renderHook(() => useSSE(params))

    // Should not throw
    act(() => {
      emit({ type: 'task_update', task_id: 't1', status: 'designing', error: null })
    })

    expect(params.setTasks).toHaveBeenCalled()
  })
})

describe('SSE fanout_triggered reloads schedule tasks', () => {
  it('calls loadScheduleTasks on fanout_triggered', () => {
    const params = makeParams()
    renderHook(() => useSSE(params))

    act(() => {
      emit({ type: 'fanout_triggered', schedule_id: 's1', batch_id: 'b1' })
    })

    expect(params.loadScheduleTasks).toHaveBeenCalled()
    expect(params.loadSchedules).toHaveBeenCalled()
    expect(params.loadTasks).toHaveBeenCalled()
  })
})

describe('SSE schedule plan-lifecycle events reload schedules', () => {
  // Regression guard: before the fix the left-panel "规划中" badge
  // and SchedulePlanPanel would stay stale after the plan committed
  // because useSSE ignored these events entirely.
  it.each([
    'schedule_plan_ready',
    'schedule_plan_timeout',
    'schedule_plan_stale',
  ])('calls loadSchedules on %s', (eventType) => {
    const params = makeParams()
    renderHook(() => useSSE(params))

    act(() => {
      emit({ type: eventType, schedule_id: 's1' })
    })

    expect(params.loadSchedules).toHaveBeenCalled()
    expect(params.loadScheduleTasks).toHaveBeenCalled()
  })
})
