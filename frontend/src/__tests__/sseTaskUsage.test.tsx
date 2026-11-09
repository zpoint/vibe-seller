/**
 * `task_usage` SSE: the live meter patches the task wherever it is on
 * screen (list, schedule list, open detail) and touches nothing else.
 *
 * Drives the real useSSE hook (same harness as sseQueuedOutputReconcile).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useState } from 'react'
import { useSSE } from '../hooks/useSSE'
import type { Task } from '../types'
import { makeTask } from '../test/factories'

type Listener = ((evt: MessageEvent) => void) | null
let esInstances: { onmessage: Listener; close: ReturnType<typeof vi.fn> }[] = []

beforeEach(() => {
  esInstances = []
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

function useHarness(initial: Task[]) {
  const [tasks, setTasks] = useState<Task[]>(initial)
  const [selected, setSelectedTask] = useState<Task | null>(initial[0])
  const [scheduleTasks, setScheduleTasks] = useState<Task[]>(initial)
  useSSE({
    selectedTaskId: initial[0]?.id,
    appView: 'tasks',
    selectedStoreId: null,
    showAllTasks: true,
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
    setScheduleTasks,
    loadScheduleTasks: vi.fn(),
    loadSchedules: vi.fn(),
    loadTasks: vi.fn(),
  })
  return { tasks, selected, scheduleTasks }
}

const USAGE = {
  type: 'task_usage',
  task_id: 't1',
  cost_usd: 0.42,
  input_tokens: 1000,
  output_tokens: 200,
  cache_read_tokens: 50_000,
  cache_write_tokens: 0,
  context_tokens: 51_200,
  context_window: 200_000,
  usage_model: 'claude-test',
  cost_basis: 'catalog',
  num_turns: 1,
}

describe('task_usage SSE', () => {
  it('patches the list, the schedule list and the open task', () => {
    const t1 = makeTask({ id: 't1', status: 'running' })
    const t2 = makeTask({ id: 't2', status: 'running' })
    const { result } = renderHook(() => useHarness([t1, t2]))
    emit(USAGE)
    for (const task of [
      result.current.tasks[0],
      result.current.scheduleTasks[0],
      result.current.selected!,
    ]) {
      expect(task.context_tokens).toBe(51_200)
      expect(task.context_window).toBe(200_000)
      expect(task.cost_usd).toBe(0.42)
      expect(task.cost_basis).toBe('catalog')
      expect(task.status).toBe('running') // nothing but usage changes
    }
    expect(result.current.tasks[1].context_tokens).toBeUndefined()
  })

  it('a later update replaces the earlier figures', () => {
    const { result } = renderHook(() => useHarness([makeTask({ id: 't1' })]))
    emit(USAGE)
    emit({ ...USAGE, context_tokens: 80_000, cost_usd: 0.9 })
    expect(result.current.selected!.context_tokens).toBe(80_000)
    expect(result.current.selected!.cost_usd).toBe(0.9)
  })

  it('ignores fields that are not usage', () => {
    const { result } = renderHook(() => useHarness([makeTask({ id: 't1', title: 'keep' })]))
    emit({ ...USAGE, title: 'hijacked', status: 'failed' })
    expect(result.current.tasks[0].title).toBe('keep')
    expect(result.current.tasks[0].status).not.toBe('failed')
  })
})
