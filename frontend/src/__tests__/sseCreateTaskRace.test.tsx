/**
 * Reproduces the race observed against the production server:
 *
 *   T=0 ms  user clicks "Create task" → POST /api/tasks (in flight)
 *   T=~50ms backend creates task (status=PENDING), dispatches it,
 *           transitions to QUEUED then RUNNING, emits task_update
 *           SSE events for each transition
 *   T=~600ms POST response resolves → frontend calls
 *           setTasks(prev => [task /* PENDING *\/, ...prev])
 *
 * The SSE task_update events for QUEUED/RUNNING arrive BEFORE the
 * POST response, so when useSSE's handler runs
 *   setTasks(prev => prev.map(t => t.id === data.task_id ? ... : t))
 * the task isn't in `prev` yet — the patch is silently dropped.
 *
 * Result: after everything settles, the tasks list shows PENDING
 * even though the DB says RUNNING. The detail view (selectedTask)
 * can be right because setSelectedTask(task) happens right after
 * the POST and then catches future updates — but any updates that
 * arrived *during* the POST round-trip are lost.
 *
 * This file exercises that exact ordering against the real useSSE
 * hook and asserts the dropped-patch behaviour, so when we fix it
 * we can flip the assertions to the corrected outcome.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useState } from 'react'
import { useSSE } from '../hooks/useSSE'
import type { Task } from '../types'

/* ── EventSource mock ──────────────────────────────── */

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

function makeTask(id: string, status = 'pending'): Task {
  return {
    id, store_id: null, title: 'T', description: null,
    status, plan: null, result: null, todos: null,
    wait_condition: null, error: null, plan_mode: false,
    ai_profile_id: null, schedule_id: null, batch_id: null,
    created_at: '', started_at: null, completed_at: null,
  }
}

/**
 * Harness that runs `useSSE` against real React state so we can
 * observe what the tasks list actually contains after a sequence
 * of SSE events + deferred POST resolution.
 */
function useRaceHarness() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [selectedTask, setSelectedTask] = useState<Task | null>(null)

  useSSE({
    selectedTaskId: selectedTask?.id,
    appView: 'tasks',
    setTasks,
    setSelectedTask,
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
  })

  return { tasks, selectedTask, setTasks, setSelectedTask }
}

/* ── The test ──────────────────────────────────────── */

describe('create-task race with SSE task_update', () => {
  it('CONFIRMS RACE: status patch is dropped when task_update arrives before the POST response adds the task', () => {
    const { result } = renderHook(() => useRaceHarness())

    // Sanity: list starts empty
    expect(result.current.tasks).toEqual([])

    // --- simulate the race ---
    // 1) Backend emits task_update(running) for the new task ID
    //    BEFORE the frontend has added it to the list.
    act(() => {
      emit({ type: 'task_update', task_id: 'new-task', status: 'running', error: null })
    })

    // 2) POST response arrives: frontend adds the task with its
    //    original PENDING status (what the API returned at T=0).
    act(() => {
      result.current.setTasks(prev => [makeTask('new-task', 'pending'), ...prev])
      result.current.setSelectedTask(makeTask('new-task', 'pending'))
    })

    // Without the race-guard re-fetch, the tasks list now holds
    // the task with status=pending because the earlier SSE patch
    // hit a map() over an empty list. The App.tsx
    // submitCreateTask handler must issue a follow-up GET to
    // reconcile (see that function for the fix).
    expect(result.current.tasks).toHaveLength(1)
    expect(result.current.tasks[0].status).toBe('pending')
  })

  it('SHOWS THE FIX: a follow-up GET that patches the list reconciles the dropped status', () => {
    const { result } = renderHook(() => useRaceHarness())

    // Same race sequence as above
    act(() => {
      emit({ type: 'task_update', task_id: 'new-task', status: 'running', error: null })
    })
    act(() => {
      result.current.setTasks(prev => [makeTask('new-task', 'pending'), ...prev])
      result.current.setSelectedTask(makeTask('new-task', 'pending'))
    })

    // Bug present — pending
    expect(result.current.tasks[0].status).toBe('pending')

    // App.tsx submitCreateTask does `api.get('/api/tasks/{id}')`
    // then patches setTasks / setSelectedTask. Simulate that.
    const latestFromApi = makeTask('new-task', 'running')
    act(() => {
      result.current.setTasks(prev =>
        prev.map(pt => (pt.id === latestFromApi.id ? { ...pt, ...latestFromApi } : pt))
      )
      result.current.setSelectedTask(prev =>
        prev && prev.id === latestFromApi.id ? { ...prev, ...latestFromApi } : prev
      )
    })

    // Now reconciled
    expect(result.current.tasks[0].status).toBe('running')
    expect(result.current.selectedTask?.status).toBe('running')
  })

  it('SHOWS THE CONTRAST: when task is already in the list, task_update patches it normally', () => {
    const { result } = renderHook(() => useRaceHarness())

    act(() => {
      result.current.setTasks([makeTask('existing', 'pending')])
    })
    act(() => {
      emit({ type: 'task_update', task_id: 'existing', status: 'running', error: null })
    })

    expect(result.current.tasks[0].status).toBe('running')
  })
})
