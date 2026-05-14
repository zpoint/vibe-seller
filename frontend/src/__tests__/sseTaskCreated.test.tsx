/**
 * task_created SSE: when another tab/user creates a task, the
 * current tab's task list updates without a refetch — but only for
 * the view the user is actually looking at, and never as a duplicate
 * of a task already in the list.
 *
 * The harness mirrors sseCreateTaskRace.test.tsx so we exercise the
 * real useSSE hook rather than a re-implementation.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useState } from 'react'
import { useSSE } from '../hooks/useSSE'
import type { Task } from '../types'

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
  // Real SSE broadcasts to every connected client — fan out to all
  // mocked EventSource instances so multi-view tests behave like
  // multiple browser tabs.
  for (const inst of esInstances) {
    inst.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }
}

function makeTask(id: string, store_id: string | null = null, status = 'pending'): Task {
  return {
    id, store_id, title: `T-${id}`, description: null,
    status, plan: null, result: null, todos: null,
    wait_condition: null, error: null, plan_mode: false,
    ai_profile_id: null, schedule_id: null, batch_id: null,
    created_at: '', started_at: null, completed_at: null,
  }
}

interface ViewProps {
  selectedStoreId: string | null
  showAllTasks: boolean
}

function useHarness({ selectedStoreId, showAllTasks }: ViewProps) {
  const [tasks, setTasks] = useState<Task[]>([])
  useSSE({
    selectedTaskId: undefined,
    appView: 'tasks',
    selectedStoreId,
    showAllTasks,
    setTasks,
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
  })
  return { tasks, setTasks }
}

describe('task_created SSE', () => {
  it('prepends a new task when its store matches the current view', () => {
    const { result } = renderHook(() => useHarness({
      selectedStoreId: 'store-A', showAllTasks: false,
    }))

    act(() => {
      emit({
        type: 'task_created',
        task_id: 'remote-1',
        store_id: 'store-A',
        task: makeTask('remote-1', 'store-A'),
      })
    })

    expect(result.current.tasks).toHaveLength(1)
    expect(result.current.tasks[0].id).toBe('remote-1')
  })

  it('ignores tasks created in a different store', () => {
    const { result } = renderHook(() => useHarness({
      selectedStoreId: 'store-A', showAllTasks: false,
    }))

    act(() => {
      emit({
        type: 'task_created',
        task_id: 'remote-2',
        store_id: 'store-B',
        task: makeTask('remote-2', 'store-B'),
      })
    })

    expect(result.current.tasks).toEqual([])
  })

  it('routes null-store tasks to the All stores view, not to a specific store', () => {
    const allView = renderHook(() => useHarness({
      selectedStoreId: null, showAllTasks: true,
    }))
    const storeView = renderHook(() => useHarness({
      selectedStoreId: 'store-A', showAllTasks: false,
    }))

    act(() => {
      emit({
        type: 'task_created',
        task_id: 'no-store-1',
        store_id: null,
        task: makeTask('no-store-1', null),
      })
    })

    expect(allView.result.current.tasks).toHaveLength(1)
    expect(storeView.result.current.tasks).toEqual([])
  })

  it('dedupes when the same tab already prepended the task from POST', () => {
    const { result } = renderHook(() => useHarness({
      selectedStoreId: 'store-A', showAllTasks: false,
    }))

    // Tab posted the task, prepended optimistically (App.tsx submitCreateTask).
    act(() => {
      result.current.setTasks(prev => [makeTask('local-1', 'store-A'), ...prev])
    })
    // Then the SSE arrives for the very same task — must not double-insert.
    act(() => {
      emit({
        type: 'task_created',
        task_id: 'local-1',
        store_id: 'store-A',
        task: makeTask('local-1', 'store-A', 'queued'),
      })
    })

    expect(result.current.tasks).toHaveLength(1)
    // Existing entry kept (not clobbered) — App.tsx owns reconciliation
    // for the local POST path via its follow-up GET.
    expect(result.current.tasks[0].status).toBe('pending')
  })

  it('REGRESSION: SSE task_created arriving BEFORE the POST response must not produce a duplicate', async () => {
    // Backend emits task_created (via event_bus) after commit but
    // BEFORE returning the POST response — see
    // app/routers/tasks.py and tests/workflow/test_wf_task_created_event.py.
    // On the originating tab the SSE event therefore can — and in
    // practice does — arrive while `await api.post(...)` is still
    // in flight, prepending the task via useSSE.  If the POST-
    // response branch then prepends naively, the list shows the
    // task twice until refresh.
    //
    // Drives the real production handler (submitCreateTask) so
    // reverting its dedup line breaks this test.
    const { submitCreateTask } = await import('../handlers/submitCreateTask')

    const { result } = renderHook(() => useHarness({
      selectedStoreId: 'store-A', showAllTasks: false,
    }))

    const created = makeTask('local-2', 'store-A', 'pending')

    // Stub api.post that DOES NOT resolve until we say so — gives
    // us a window in which to fire the SSE the way the real server
    // does (event_bus.emit before return).
    let resolvePost: (t: Task) => void = () => {}
    const postPromise = new Promise<Task>(res => { resolvePost = res })
    const apiStub = {
      post: vi.fn(() => postPromise),
      get: vi.fn(() => Promise.resolve(created)),
    }

    let submitDone: Promise<Task> | undefined
    act(() => {
      submitDone = submitCreateTask(
        { title: 'T', description: '', files: [] },
        {
          api: apiStub,
          storeId: 'store-A',
          planMode: false,
          setTasks: result.current.setTasks,
          setSelectedTask: vi.fn(),
        },
      )
    })

    // Backend committed and emitted task_created — POST still in flight.
    act(() => {
      emit({
        type: 'task_created',
        task_id: created.id,
        store_id: 'store-A',
        task: { ...created, status: 'queued' },
      })
    })
    expect(result.current.tasks).toHaveLength(1)

    // POST response now arrives at the client.
    await act(async () => {
      resolvePost(created)
      await submitDone
    })

    // Must still be exactly one row, not two.
    expect(result.current.tasks).toHaveLength(1)
    expect(result.current.tasks[0].id).toBe(created.id)
  })
})
