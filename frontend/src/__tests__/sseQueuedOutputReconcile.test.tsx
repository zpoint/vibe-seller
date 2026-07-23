/**
 * Reproduces the "Queued badge + streaming output + disabled input"
 * desync observed in production.
 *
 *   backend flips task → RUNNING and emits task_update(running)
 *   ...but that update is missed / late / clobbered on the client
 *      (the race class already documented in retryTaskRace +
 *       sseCreateTaskRace)
 *   agent streams task_message events (thinking / tool_use / prose)
 *   → the list badge stays 'queued', which maps to
 *     canSendMessage:false (taskStates.ts) — the input bar is greyed
 *     out even though the agent is plainly running.
 *
 * The invariant this pins: **a task that is emitting session messages
 * is active — it can never remain 'queued'/'pending'.** useSSE
 * reconciles defensively on every task_message so a lost RUNNING
 * update can't strand the badge. It must promote ONLY from the
 * not-started states and never disturb completed/waiting/failed.
 *
 * Drives the real useSSE hook (same harness as sseTaskCreated).
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
  act(() => {
    for (const inst of esInstances) {
      inst.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
    }
  })
}

function makeTask(id: string, status = 'queued', plan_mode = false): Task {
  return {
    id, store_id: null, title: `T-${id}`, description: null,
    status, plan: null, result: null, todos: null,
    wait_condition: null, error: null, plan_mode,
    ai_profile_id: null, schedule_id: null, batch_id: null,
    created_at: '', started_at: null, completed_at: null,
  }
}

function useHarness(initial: Task[]) {
  const [tasks, setTasks] = useState<Task[]>(initial)
  useSSE({
    selectedTaskId: undefined,
    appView: 'tasks',
    selectedStoreId: null,
    showAllTasks: true,
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
  return { tasks }
}

describe('queued→running reconciliation on streamed output', () => {
  it('promotes a queued task to running when it streams a message', () => {
    const { result } = renderHook(() => useHarness([makeTask('t1', 'queued')]))
    emit({ type: 'task_message', task_id: 't1', role: 'assistant', content: 'working…' })
    expect(result.current.tasks[0].status).toBe('running')
  })

  it('promotes a pending task too (any agent-origin message)', () => {
    const { result } = renderHook(() => useHarness([makeTask('t1', 'pending')]))
    emit({ type: 'task_message', task_id: 't1', role: 'thinking', content: 'hmm' })
    expect(result.current.tasks[0].status).toBe('running')
  })

  it('promotes a plan-mode task to designing, not running', () => {
    const { result } = renderHook(() => useHarness([makeTask('t1', 'queued', true)]))
    emit({ type: 'task_message', task_id: 't1', role: 'assistant', content: 'planning…' })
    expect(result.current.tasks[0].status).toBe('designing')
  })

  it('never disturbs a terminal/active status (completed stays completed)', () => {
    const { result } = renderHook(() => useHarness([makeTask('t1', 'completed')]))
    // e.g. optimistic echo of a follow-up message on a done task
    emit({ type: 'task_message', task_id: 't1', role: 'user', content: 'thanks' })
    expect(result.current.tasks[0].status).toBe('completed')
  })

  it('leaves a waiting task waiting (a live question is not "running")', () => {
    const { result } = renderHook(() => useHarness([makeTask('t1', 'waiting')]))
    emit({ type: 'task_message', task_id: 't1', role: 'assistant', content: 'anything else?' })
    expect(result.current.tasks[0].status).toBe('waiting')
  })
})
