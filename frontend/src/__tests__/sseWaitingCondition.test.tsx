/**
 * A task that goes WAITING live shows what it waits for.
 *
 * The task_update event carries the status only; the badge and the
 * waiting card are drawn from `wait_condition`, so the hook reads it
 * from the task row — otherwise they appear only after a reload.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'

vi.mock('../api', () => ({ api: { get: vi.fn() } }))

import { api } from '../api'
import { useSSE } from '../hooks/useSSE'
import type { Task } from '../types'
import { makeTask } from '../test/factories'

type Listener = ((evt: MessageEvent) => void) | null
let esInstances: { onmessage: Listener; close: ReturnType<typeof vi.fn> }[] = []

beforeEach(() => {
  vi.mocked(api.get).mockReset()
  esInstances = []
  vi.stubGlobal('EventSource', vi.fn().mockImplementation(function () {
    const inst = { onmessage: null as Listener, close: vi.fn() }
    esInstances.push(inst)
    return inst
  }))
})

afterEach(() => {
  vi.restoreAllMocks()
})

function emit(data: Record<string, unknown>) {
  esInstances[esInstances.length - 1]?.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
}

function params() {
  return {
    selectedTaskId: 'p1',
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
  } as unknown as Parameters<typeof useSSE>[0]
}

describe('a task entering WAITING live', () => {
  it('gets its wait_condition from the task row', async () => {
    const cond = JSON.stringify({ strategy: 'children' })
    vi.mocked(api.get).mockResolvedValue(makeTask({ id: 'p1', status: 'waiting', wait_condition: cond }))
    const p = params()
    renderHook(() => useSSE(p))

    act(() => emit({ type: 'task_update', task_id: 'p1', status: 'waiting' }))

    expect(api.get).toHaveBeenCalledWith('/api/tasks/p1')
    const setSelected = p.setSelectedTask as unknown as ReturnType<typeof vi.fn>
    await waitFor(() => {
      const selected = makeTask({ id: 'p1', status: 'waiting' })
      const got = setSelected.mock.calls
        .map(c => (c[0] as (t: Task) => Task)(selected))
        .find(t => t.wait_condition === cond)
      expect(got).toBeTruthy()
    })
  })

  it('other status changes do not re-read the row', () => {
    const p = params()
    renderHook(() => useSSE(p))
    act(() => emit({ type: 'task_update', task_id: 'p1', status: 'running' }))
    expect(api.get).not.toHaveBeenCalledWith('/api/tasks/p1')
  })
})

describe('a task failing live', () => {
  it('takes its error category with its error, as a reload would', async () => {
    const p = params()
    renderHook(() => useSSE(p))
    act(() => emit({
      type: 'task_update', task_id: 'p1', status: 'failed',
      error: 'Nobody replied within 7 days, so the task stopped.',
      error_category: 'wait_timeout',
    }))
    const setSelected = p.setSelectedTask as unknown as ReturnType<typeof vi.fn>
    await waitFor(() => {
      const before = makeTask({ id: 'p1', status: 'waiting' })
      const got = setSelected.mock.calls
        .map(c => (c[0] as (t: Task) => Task)(before))
        .find(t => t.status === 'failed')
      expect(got?.error_category).toBe('wait_timeout')
      expect(got?.error).toContain('Nobody replied')
    })
  })

  it('retires a parked question the open page still shows', () => {
    const p = params()
    renderHook(() => useSSE(p))
    act(() => emit({
      type: 'task_update', task_id: 'p1', status: 'failed',
      error: 'Nobody replied within 7 days, so the task stopped.',
      error_category: 'wait_timeout',
    }))
    expect(p.setPendingQuestions).toHaveBeenCalledWith(null)
  })

  it('a status that can still be asked leaves the question alone', () => {
    const p = params()
    renderHook(() => useSSE(p))
    act(() => emit({ type: 'task_update', task_id: 'p1', status: 'waiting' }))
    expect(p.setPendingQuestions).not.toHaveBeenCalled()
  })
})
