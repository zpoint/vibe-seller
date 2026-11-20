/**
 * A question nobody answered parks the task in WAITING and stops its
 * agent. The agent's exit arrives as `agent_done`, which clears the
 * question banner — but the question is still answerable (answering
 * resumes the task), so the server follows up with
 * `task_question_parked`.
 *
 * Pins: after task_questions → agent_done → task_question_parked the
 * banner shows that question again, and the conversation holds the
 * question item exactly once.
 *
 * Drives the real useSSE hook (same harness as sseQueuedOutputReconcile).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useState } from 'react'
import type { ConversationItem, Task } from '../types'
import { makeTask } from '../test/factories'

const mockApi = vi.hoisted(() => ({
  get: vi.fn(() => new Promise(() => {})),
  post: vi.fn(),
}))
vi.mock('../api', () => ({ api: mockApi }))

import { useSSE } from '../hooks/useSSE'

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

type Pending = { request_id: string; questions: { question: string }[] } | null

function useHarness(task: Task) {
  const [tasks, setTasks] = useState<Task[]>([task])
  const [selectedTask, setSelectedTask] = useState<Task | null>(task)
  const [pending, setPendingQuestions] = useState<Pending>(null)
  const [items, setConversationItems] = useState<ConversationItem[]>([])
  useSSE({
    selectedTaskId: task.id,
    appView: 'tasks',
    selectedStoreId: null,
    showAllTasks: true,
    setTasks,
    setSelectedTask: setSelectedTask as never,
    setSteps: vi.fn(),
    setScreenshots: vi.fn(),
    setAgentMessages: vi.fn(),
    setTodoItems: vi.fn(),
    setConversationItems: setConversationItems as never,
    setPendingQuestions: setPendingQuestions as never,
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
  return { tasks, selectedTask, pending, items }
}

const QUESTIONS = [{ question: 'Which shipping option?' }]

describe('a parked question stays answerable', () => {
  it('re-arms the banner after agent_done without duplicating the item', () => {
    const task = makeTask({ id: 't1', status: 'running', created_at: '' })
    const { result } = renderHook(() => useHarness(task))

    emit({ type: 'task_questions', task_id: 't1', request_id: 'r1', questions: QUESTIONS })
    expect(result.current.pending?.request_id).toBe('r1')

    emit({ type: 'agent_done', task_id: 't1', success: false })
    expect(result.current.pending).toBeNull()

    emit({ type: 'task_question_parked', task_id: 't1', request_id: 'r1', questions: QUESTIONS })
    expect(result.current.pending?.request_id).toBe('r1')
    const questionItems = result.current.items.filter(it => it.type === 'question')
    expect(questionItems).toHaveLength(1)
    expect(questionItems[0].id).toBe('question-r1')
  })

  it('adds the question item when the page never saw it asked', () => {
    const task = makeTask({ id: 't1', status: 'running', created_at: '' })
    const { result } = renderHook(() => useHarness(task))
    emit({ type: 'task_question_parked', task_id: 't1', request_id: 'r2', questions: QUESTIONS })
    expect(result.current.pending?.request_id).toBe('r2')
    expect(result.current.items.filter(it => it.type === 'question')).toHaveLength(1)
  })

  it('ignores a parked question of another task', () => {
    const task = makeTask({ id: 't1', status: 'running', created_at: '' })
    const { result } = renderHook(() => useHarness(task))
    emit({ type: 'task_question_parked', task_id: 'other', request_id: 'r3', questions: QUESTIONS })
    expect(result.current.pending).toBeNull()
  })
})
