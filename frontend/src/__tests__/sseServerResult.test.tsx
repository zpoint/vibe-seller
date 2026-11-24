/**
 * A result the server sets shows live, as it does after a reload.
 *
 * A parent completes with its subtasks' combined answer: no agent
 * message carries it, only the task_update's `result`. The thread on
 * screen must show that answer, not the parent's earlier "the counts
 * will be in the subtasks" reply.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

import { useSSE } from '../hooks/useSSE'
import { buildConversationItems } from '../lib/conversation'
import type { ConversationItem } from '../types'
import { makeTask } from '../test/factories'

type Listener = ((evt: MessageEvent) => void) | null
let esInstances: { onmessage: Listener; close: ReturnType<typeof vi.fn> }[] = []

beforeEach(() => {
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

const EARLIER = 'The counts will be in the subtasks when they finish.'
const COMBINED = '### ✗ store-a\n\nNot signed in.\n\n### ✓ store-b\n\n12 open orders.'

describe('a server-set result on the open task', () => {
  it('replaces the latest result card live, matching a reload', () => {
    const p = params()
    renderHook(() => useSSE(p))
    act(() => emit({ type: 'task_update', task_id: 'p1', status: 'completed', result: COMBINED }))

    const shown: ConversationItem[] = [
      { id: 'u', type: 'user_message', timestamp: 't0', message: { role: 'user', content: 'Count orders' } },
      { id: 'r', type: 'result', timestamp: 't1', result: EARLIER },
    ]
    const setItems = p.setConversationItems as unknown as ReturnType<typeof vi.fn>
    const live = setItems.mock.calls.reduce(
      (items: ConversationItem[], c) => (typeof c[0] === 'function' ? c[0](items) : items),
      shown,
    )
    const results = live.filter(i => i.type === 'result')
    expect(results).toHaveLength(1)
    expect(results[0].result).toBe(COMBINED)
    expect(results[0].timestamp).toBe('t1')

    // Same thing a reload draws from the stored row.
    const reloaded = buildConversationItems(
      [
        { role: 'user', content: 'Count orders', created_at: 't0' },
        { role: 'result', content: EARLIER, created_at: 't1' },
      ] as Parameters<typeof buildConversationItems>[0],
      makeTask({ id: 'p1', status: 'completed', result: COMBINED }),
    )
    expect(reloaded.filter(i => i.type === 'result').map(i => i.result)).toEqual([COMBINED])
  })

  it('a task_update without a result leaves the thread alone', () => {
    const p = params()
    renderHook(() => useSSE(p))
    act(() => emit({ type: 'task_update', task_id: 'p1', status: 'running' }))
    const setItems = p.setConversationItems as unknown as ReturnType<typeof vi.fn>
    const shown: ConversationItem[] = [{ id: 'r', type: 'result', timestamp: 't1', result: EARLIER }]
    const after = setItems.mock.calls.reduce(
      (items: ConversationItem[], c) => (typeof c[0] === 'function' ? c[0](items) : items),
      shown,
    )
    expect(after.filter(i => i.type === 'result').map(i => i.result)).toEqual([EARLIER])
  })
})
