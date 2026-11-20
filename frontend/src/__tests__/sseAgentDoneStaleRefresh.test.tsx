/**
 * A task that finishes refreshes its row — but only while it is still
 * the open task.
 *
 * Observed (CI e2e): task B finished at the moment the user clicked
 * task A. B's `agent_done` refetched B, and when that reply landed it
 * set B as the selected task again — the page showed B's header over
 * A's conversation. The refresh must not change which task is open.
 *
 * Drives the real useSSE hook (same harness as sseQuestionParked).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useState } from 'react'
import type { Task } from '../types'
import { makeTask } from '../test/factories'

const pendingGets = vi.hoisted(() => [] as { url: string; resolve: (t: unknown) => void }[])
const mockApi = vi.hoisted(() => ({
  get: vi.fn((url: string) => new Promise(resolve => { pendingGets.push({ url, resolve }) })),
  post: vi.fn(),
}))
vi.mock('../api', () => ({ api: mockApi }))

import { useSSE } from '../hooks/useSSE'

type Listener = ((evt: MessageEvent) => void) | null
let esInstances: { onmessage: Listener; close: ReturnType<typeof vi.fn> }[] = []

beforeEach(() => {
  esInstances = []
  pendingGets.length = 0
  const MockES = vi.fn().mockImplementation(function () {
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
  act(() => {
    for (const inst of esInstances) {
      inst.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
    }
  })
}

function useHarness(initial: Task, all: Task[]) {
  const [tasks, setTasks] = useState<Task[]>(all)
  const [selectedTask, setSelectedTask] = useState<Task | null>(initial)
  useSSE({
    selectedTaskId: selectedTask?.id,
    appView: 'tasks',
    selectedStoreId: null,
    showAllTasks: true,
    setTasks,
    setSelectedTask: setSelectedTask as never,
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
  return { tasks, selectedTask, setSelectedTask }
}

describe('agent_done refresh of the open task', () => {
  it('does not reopen a task the user has left meanwhile', async () => {
    const a = makeTask({ id: 'A', status: 'waiting', title: 'Task A', created_at: '' })
    const b = makeTask({ id: 'B', status: 'running', title: 'Task B', created_at: '' })
    const { result } = renderHook(() => useHarness(b, [a, b]))

    emit({ type: 'agent_done', task_id: 'B', success: true })
    expect(pendingGets.map(g => g.url)).toContain('/api/tasks/B')

    // The user opens A before B's refresh comes back.
    act(() => { result.current.setSelectedTask(a) })
    await act(async () => {
      pendingGets.find(g => g.url === '/api/tasks/B')!.resolve({ ...b, status: 'completed' })
    })

    expect(result.current.selectedTask?.id).toBe('A')
    // The list still learns that B completed.
    expect(result.current.tasks.find(t => t.id === 'B')?.status).toBe('completed')
  })

  it('still refreshes the task while it stays open', async () => {
    const b = makeTask({ id: 'B', status: 'running', title: 'Task B', created_at: '' })
    const { result } = renderHook(() => useHarness(b, [b]))
    emit({ type: 'agent_done', task_id: 'B', success: true })
    await act(async () => {
      pendingGets.find(g => g.url === '/api/tasks/B')!.resolve({ ...b, status: 'completed' })
    })
    expect(result.current.selectedTask?.status).toBe('completed')
  })
})
